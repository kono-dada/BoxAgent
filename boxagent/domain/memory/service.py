"""Canonical long-term-memory state machine and user-facing operations."""

import asyncio
import time
from dataclasses import replace
from datetime import datetime, timezone

from boxagent.core.ids import new_memory_id
from boxagent.domain.memory.contracts import MemoryBackend, MemoryUnavailable
from boxagent.domain.memory.evidence import public_memory_items
from boxagent.domain.memory.models import CanonicalMemory, MemoryCandidate
from boxagent.domain.memory.policy import (
    MEMORY_QUERY_LIMIT,
    MEMORY_TEXT_LIMIT,
    canonical_slot,
    contains_secret,
    memory_admission,
    require_memory_ids,
    require_text,
)


class MemoryService:
    """Own the canonical record first; Jev is only a rebuildable search index."""

    def __init__(self, backend: MemoryBackend | None, ledger=None,
                 projection_callback=None, index_enqueue=None):
        self.backend = backend
        self.ledger = ledger
        self.projection_callback = projection_callback
        self.index_enqueue = index_enqueue
        self.recalled_memory_ids: set[str] = set()
        self.recalled_memory_content: dict[str, str] = {}
        self.mutation_lock = asyncio.Lock()

    def _require_backend(self) -> MemoryBackend:
        if self.backend is None:
            raise MemoryUnavailable("长期记忆索引未启用")
        return self.backend

    async def remember(self, content, *, source="explicit", session_id="",
                       interaction_id="", source_event_ids=()):
        content = require_text(content, name="content", limit=MEMORY_TEXT_LIMIT)
        if contains_secret(content):
            raise ValueError("密码、Token、API Key 或验证码不能写入长期记忆")
        if self.ledger is None:
            result = await self._require_backend().remember([
                {"content": content, "metadata": {"source": source}}])
            admitted = result.get("admitted", 0)
            return {"status": "succeeded" if admitted else "not_stored",
                    "message": "已记住。" if admitted else "这条信息未被写入长期记忆。",
                    "admitted": admitted, "rejected": result.get("rejected", 0),
                    "memory_count": result.get("memory_count")}
        candidate = MemoryCandidate(
            content=content, subject="user", kind="profile",
            durability="long_term", operation="add", confidence=1.0,
            sensitivity="normal",
            evidence=tuple({"event_id": item, "quote": content}
                           for item in source_event_ids))
        result = await self.admit_candidate(
            candidate, status="active", source_mode=source,
            session_id=session_id, interaction_id=interaction_id,
            source_event_ids=tuple(source_event_ids),
            defer_index=self.index_enqueue is not None)
        if result["outcome"] == "duplicate":
            return {"status": "succeeded", "message": "这条记忆已经存在。",
                    "admitted": 0, "rejected": 0,
                    "memory_count": len(await self._current_memories())}
        memory = result["memory"]
        if self.index_enqueue is not None \
                and memory.metadata.get("index_state") == "pending":
            self.index_enqueue(memory.memory_id)
        return {
            "status": "succeeded",
            "message": "已记住。" if memory.status == "active"
            else "记忆已安全落盘，搜索索引正在后台建立。",
            "admitted": 1, "rejected": 0,
            "memory_id": memory.memory_id,
            "memory_count": len(await self._current_memories()),
        }

    async def admit_candidate(self, candidate, *, status=None, source_mode,
                              session_id, interaction_id, source_event_ids,
                              defer_index=False):
        """Admit, deduplicate, supersede and index one candidate."""
        async with self.mutation_lock:
            return await self._admit_candidate_locked(
                candidate, status=status, source_mode=source_mode,
                session_id=session_id, interaction_id=interaction_id,
                source_event_ids=source_event_ids, defer_index=defer_index)

    async def _admit_candidate_locked(self, candidate, *, status, source_mode,
                                      session_id, interaction_id,
                                      source_event_ids, defer_index):
        if self.ledger is None:
            raise MemoryUnavailable("Canonical Memory Ledger 未启用")
        status = status or memory_admission(candidate)
        existing = await self.ledger.memories()
        normalized = self._normalize(candidate.content)
        duplicate = next((item for item in existing
                          if item.status in {"active", "index_failed", "pending_review"}
                          and self._normalize(item.content) == normalized), None)
        if duplicate:
            return {"outcome": "duplicate", "memory": duplicate}

        slot = canonical_slot(candidate)
        current = next((item for item in existing
                        if item.canonical_slot == slot
                        and item.status in {"active", "index_failed"}), None)
        now = time.time()
        metadata = {"index_state": "not_requested",
                    "operation": candidate.operation}
        if current:
            metadata["proposed_supersedes_id"] = current.memory_id
        memory = CanonicalMemory(
            memory_id=new_memory_id(), revision=1,
            content=candidate.content.strip(), subject=candidate.subject,
            kind=candidate.kind, status=status,
            confidence=candidate.confidence,
            sensitivity=candidate.sensitivity, source_mode=source_mode,
            source_session_id=session_id,
            source_interaction_id=interaction_id,
            source_event_ids=tuple(dict.fromkeys(source_event_ids)),
            created_at=now, updated_at=now,
            expires_at=candidate.expires_at,
            metadata=metadata, canonical_slot=slot,
            supersedes_revision=current.revision if current else None,
            lineage_event_ids=tuple(dict.fromkeys(
                (*current.lineage_event_ids, *current.source_event_ids,
                 *source_event_ids))) if current else tuple(source_event_ids),
        )
        await self.ledger.save_memory(memory)
        if status == "active" and defer_index:
            memory = replace(
                memory, revision=memory.revision + 1, status="active",
                updated_at=time.time(),
                metadata={**memory.metadata, "index_state": "pending",
                          "index_attempts": 0,
                          "index_next_retry_at": time.time()})
            await self.ledger.save_memory(memory)
        elif status == "active":
            memory = await self._activate(memory, current)
        await self._refresh_projection()
        return {"outcome": status, "memory": memory}

    async def pending_review(self):
        return [self._record_payload(item) for item in
                await self.ledger.memories(statuses={"pending_review"})]

    async def approve(self, memory_id):
        async with self.mutation_lock:
            return await self._approve_locked(memory_id)

    async def _approve_locked(self, memory_id):
        memory = await self._require_record(memory_id, {"pending_review"})
        supersedes_id = memory.metadata.get("proposed_supersedes_id")
        supersedes = await self.ledger.memory(supersedes_id) if supersedes_id else None
        activated = await self._activate(memory, supersedes)
        await self._refresh_projection()
        return {"status": "succeeded", "memory": self._record_payload(activated)}

    async def reject(self, memory_id):
        async with self.mutation_lock:
            return await self._reject_locked(memory_id)

    async def _reject_locked(self, memory_id):
        memory = await self._require_record(memory_id, {"pending_review"})
        updated = replace(
            memory, revision=memory.revision + 1, status="rejected",
            updated_at=time.time(),
            metadata={**memory.metadata, "review_decision": "rejected"})
        await self.ledger.save_memory(updated)
        await self._refresh_projection()
        return {"status": "succeeded", "memory": self._record_payload(updated)}

    async def retry_index(self, memory_id):
        async with self.mutation_lock:
            return await self._retry_index_locked(memory_id)

    async def _retry_index_locked(self, memory_id):
        memory = await self._require_record(memory_id, {"index_failed", "active"})
        updated = await self._index(memory)
        await self._refresh_projection()
        return {"status": "succeeded" if updated.status == "active" else "index_failed",
                "memory": self._record_payload(updated)}

    async def _activate(self, memory, supersedes=None):
        if supersedes and supersedes.memory_id != memory.memory_id \
                and supersedes.status in {"active", "index_failed"}:
            superseded = replace(
                supersedes, revision=supersedes.revision + 1,
                status="superseded", updated_at=time.time(),
                metadata={**supersedes.metadata, "superseded_by": memory.memory_id})
            await self.ledger.save_memory(superseded)
            if supersedes.backend_links and self.backend is not None:
                try:
                    await self.backend.forget(list(supersedes.backend_links))
                except Exception as exc:  # noqa: BLE001 - canonical supersede already committed
                    await self.ledger.save_memory(replace(
                        superseded, revision=superseded.revision + 1,
                        updated_at=time.time(),
                        metadata={**superseded.metadata,
                                  "index_cleanup_state": "failed",
                                  "index_cleanup_error": type(exc).__name__}))
        activated = replace(
            memory, revision=memory.revision + 1, status="active",
            updated_at=time.time(),
            metadata={**memory.metadata, "review_decision": "approved",
                      "index_state": "pending"})
        await self.ledger.save_memory(activated)
        return await self._index(activated)

    async def _index(self, memory):
        attempts = int(memory.metadata.get("index_attempts") or 0) + 1
        try:
            result = await self._require_backend().remember([{
                "content": memory.content,
                "metadata": {
                    "source": memory.source_mode,
                    "canonical_memory_id": memory.memory_id,
                    "canonical_slot": memory.canonical_slot,
                    "kind": memory.kind,
                    "session_id": memory.source_session_id,
                    "interaction_id": memory.source_interaction_id,
                },
            }])
            indexed = bool(result.get("admitted"))
            links = tuple(str(item.get("id")) for item in result.get("created", [])
                          if isinstance(item, dict) and item.get("id"))
        except Exception as exc:  # noqa: BLE001 - backend failures become durable state
            indexed, links, error = False, (), type(exc).__name__
        else:
            error = "" if indexed else "backend_rejected"
        delay = min(300, 2 ** min(attempts - 1, 8))
        updated = replace(
            memory, revision=memory.revision + 1,
            status="active" if indexed else "index_failed",
            updated_at=time.time(), backend_links=links or memory.backend_links,
            metadata={**memory.metadata,
                      "index_state": "indexed" if indexed else "failed",
                      "index_attempts": attempts, "index_error": error,
                      "index_next_retry_at": None if indexed else time.time() + delay})
        await self.ledger.save_memory(updated)
        return updated

    async def recall(self, query, *, top_k=5):
        query = require_text(query, name="query", limit=MEMORY_QUERY_LIMIT)
        if self.ledger is None:
            result = await self._require_backend().query(query, top_k=top_k)
            memories = public_memory_items(result.get("memories", []))
            self.recalled_memory_ids.update(
                item["id"] for item in memories if item.get("id"))
            self.recalled_memory_content.update(
                {item["id"]: item["content"] for item in memories
                 if item.get("id") and item.get("content")})
            return {"status": "succeeded",
                    "message": "找到了相关记忆。" if memories else "没有找到相关记忆。",
                    "memories": memories}
        records = await self._current_memories()
        by_content = {item.content: item for item in records}
        memories = []
        if self.backend is not None:
            try:
                result = await self.backend.query(query, top_k=top_k)
                for item in public_memory_items(result.get("memories", [])):
                    canonical = by_content.get(item.get("content"))
                    if canonical:
                        memories.append({"id": canonical.memory_id,
                                         "content": canonical.content,
                                         "timestamp": item.get("timestamp")})
            except Exception:  # noqa: BLE001 - canonical fallback is intentional
                memories = []
        if not memories:
            terms = {char for char in query if not char.isspace()}
            ranked = sorted(records,
                            key=lambda item: len(terms & set(item.content)),
                            reverse=True)
            memories = [{"id": item.memory_id, "content": item.content,
                         "timestamp": None}
                        for item in ranked
                        if len(terms & set(item.content)) > 0][:top_k]
        memories = memories[:top_k]
        self.recalled_memory_ids.update(item["id"] for item in memories)
        self.recalled_memory_content.update(
            {item["id"]: item["content"] for item in memories})
        return {"status": "succeeded",
                "message": "找到了相关记忆。" if memories else "没有找到相关记忆。",
                "memories": memories}

    async def snapshot(self, *, query="", selected_id=None,
                       node_limit=100, edge_limit=200, statuses=None):
        if self.ledger is None:
            return await self._require_backend().inspect(
                query=query, selected_id=selected_id,
                node_limit=node_limit, edge_limit=edge_limit)
        records = await self.ledger.memories()
        if statuses:
            wanted = set(statuses)
            records = [item for item in records if item.status in wanted]
        if query:
            needle = query.casefold()
            records = [item for item in records if needle in (
                f"{item.content} {item.kind} {item.status} {item.source_mode} "
                f"{item.memory_id} {item.canonical_slot}").casefold()]
        if selected_id:
            selected = next((item for item in records
                             if item.memory_id == selected_id), None)
            records = [selected] if selected else []
        truncated = len(records) > node_limit
        records = records[:node_limit]
        nodes = [self._record_node(item) for item in records]
        edges, backend_error = [], ""
        if self.backend is not None:
            all_records = await self.ledger.memories()
            records_by_id = {item.memory_id: item for item in all_records}
            link_map = {link: item.memory_id for item in all_records
                        for link in item.backend_links}
            backend_selected = None
            if selected_id:
                selected_record = next((item for item in all_records
                                        if item.memory_id == selected_id), None)
                backend_selected = (selected_record.backend_links[0]
                                    if selected_record and selected_record.backend_links else None)
            try:
                if selected_id and backend_selected is None:
                    raise MemoryUnavailable("该 Canonical 记忆尚未建立图索引")
                graph = await self.backend.inspect(
                    query=query, selected_id=backend_selected,
                    node_limit=node_limit, edge_limit=edge_limit)
                for edge in graph.get("edges", []):
                    source = link_map.get(edge.get("source"), edge.get("source"))
                    target = link_map.get(edge.get("target"), edge.get("target"))
                    if source and target:
                        edges.append({**edge, "source": source, "target": target})
                known = {item["id"] for item in nodes}
                for node in graph.get("nodes", []):
                    node_id = link_map.get(node.get("id"), node.get("id"))
                    if node_id in known:
                        continue
                    canonical = records_by_id.get(node_id)
                    if canonical:
                        nodes.append(self._record_node(canonical))
                    else:
                        nodes.append({**node, "id": node_id,
                                      "status": "index_only"})
                    known.add(node_id)
            except Exception as exc:  # noqa: BLE001 - dashboard remains usable without Jev
                backend_error = type(exc).__name__
        jobs = await self.ledger.jobs()
        status_counts, job_counts = {}, {}
        for item in await self.ledger.memories():
            status_counts[item.status] = status_counts.get(item.status, 0) + 1
        for item in jobs:
            job_counts[item.status] = job_counts.get(item.status, 0) + 1
        return {
            "nodes": nodes[:node_limit], "edges": edges[:edge_limit],
            "selected_id": selected_id,
            "truncated": truncated or len(nodes) > node_limit,
            "statistics": {
                "node_count": len(await self.ledger.memories()),
                "edge_count": len(edges), "matched_count": len(nodes[:node_limit]),
                "status_counts": status_counts, "job_counts": job_counts,
                "backend_error": backend_error,
            },
        }

    async def delete_node(self, memory_id):
        memory_id = require_text(memory_id, name="memory_id", limit=200)
        if self.ledger is None:
            result = await self._require_backend().forget([memory_id])
            deleted = public_memory_items(result.get("deleted", []))
            self.recalled_memory_ids.discard(memory_id)
            return {"status": "succeeded" if deleted else "not_found",
                    "deleted": deleted, "missing": result.get("missing", []),
                    "memory_count": result.get("memory_count")}
        async with self.mutation_lock:
            deleted, backend_ids = await self._tombstone_canonical([memory_id])
        if backend_ids and self.backend is not None:
            try:
                await self.backend.forget(backend_ids)
            except Exception as exc:  # noqa: BLE001 - tombstone must remain authoritative
                await self._mark_cleanup_failure(deleted, exc)
        self.recalled_memory_ids.discard(memory_id)
        await self._refresh_projection()
        return {"status": "succeeded" if deleted else "not_found",
                "deleted": [self._record_payload(item) for item in deleted],
                "missing": [] if deleted else [memory_id],
                "memory_count": len(await self._current_memories())}

    async def forget(self, memory_ids):
        memory_ids = require_memory_ids(memory_ids)
        unseen = [item for item in memory_ids if item not in self.recalled_memory_ids]
        if unseen:
            raise ValueError("删除前必须先检索并使用 recall_memory 返回的精确 ID")
        if self.ledger is None:
            result = await self._require_backend().forget(memory_ids)
            deleted = public_memory_items(result.get("deleted", []))
            self.recalled_memory_ids.difference_update(
                item["id"] for item in deleted if item.get("id"))
            return {"status": "succeeded" if deleted else "not_found",
                    "message": f"已删除 {len(deleted)} 条记忆。" if deleted
                    else "没有找到可删除的记忆。",
                    "deleted": deleted, "missing": result.get("missing", []),
                    "memory_count": result.get("memory_count")}
        async with self.mutation_lock:
            deleted, backend_ids = await self._tombstone_canonical(memory_ids)
        if backend_ids and self.backend is not None:
            try:
                await self.backend.forget(backend_ids)
            except Exception as exc:  # noqa: BLE001 - tombstone must remain authoritative
                await self._mark_cleanup_failure(deleted, exc)
        self.recalled_memory_ids.difference_update(memory_ids)
        await self._refresh_projection()
        return {"status": "succeeded" if deleted else "not_found",
                "message": f"已删除 {len(deleted)} 条记忆。" if deleted
                else "没有找到可删除的记忆。",
                "deleted": [self._record_payload(item) for item in deleted],
                "missing": [item for item in memory_ids
                            if item not in {record.memory_id for record in deleted}],
                "memory_count": len(await self._current_memories())}

    async def _tombstone_canonical(self, memory_ids):
        if self.ledger is None:
            return [], []
        ids, deleted, backend_ids = set(memory_ids), [], []
        records = await self.ledger.memories()
        for record in records:
            if record.memory_id not in ids or record.status == "deleted":
                continue
            updated = replace(
                record, revision=record.revision + 1, status="deleted",
                updated_at=time.time(),
                metadata={**record.metadata, "deleted_via": "user",
                          "index_state": "deleted"})
            await self.ledger.save_memory(updated)
            deleted.append(updated)
            backend_ids.extend(record.backend_links)
        return deleted, list(dict.fromkeys(backend_ids))

    async def _mark_cleanup_failure(self, records, exc):
        for record in records:
            current = await self.ledger.memory(record.memory_id)
            if current is None or current.status != "deleted":
                continue
            await self.ledger.save_memory(replace(
                current, revision=current.revision + 1,
                updated_at=time.time(),
                metadata={**current.metadata,
                          "index_cleanup_state": "failed",
                          "index_cleanup_error": type(exc).__name__}))

    async def _require_record(self, memory_id, statuses):
        memory_id = require_text(memory_id, name="memory_id", limit=200)
        record = await self.ledger.memory(memory_id)
        if record is None:
            raise ValueError("记忆不存在")
        if record.status not in statuses:
            raise ValueError(f"当前记忆状态 {record.status} 不支持该操作")
        return record

    async def _current_memories(self):
        if self.ledger is None:
            return []
        return await self.ledger.memories(statuses={"active", "index_failed"})

    async def _refresh_projection(self):
        if self.projection_callback is None:
            return
        value = self.projection_callback()
        if asyncio.iscoroutine(value):
            await value

    @staticmethod
    def _normalize(content):
        return "".join(str(content).casefold().split())

    @staticmethod
    def _record_payload(item):
        return {key: value for key, value in item.payload().items()
                if key != "schema_version"}

    @staticmethod
    def _record_node(item):
        stamp = datetime.fromtimestamp(item.updated_at, timezone.utc).isoformat()
        return {
            "id": item.memory_id, "type": item.kind.upper(),
            "content": item.content, "timestamp": stamp,
            "source": item.source_mode, "status": item.status,
            "kind": item.kind, "revision": item.revision,
            "canonical_slot": item.canonical_slot,
            "source_session_id": item.source_session_id,
            "source_interaction_id": item.source_interaction_id,
            "source_event_ids": list(item.source_event_ids),
            "backend_links": list(item.backend_links),
            "index_state": item.metadata.get("index_state", "unknown"),
            "index_attempts": int(item.metadata.get("index_attempts") or 0),
            "index_error": item.metadata.get("index_error", ""),
            "index_cleanup_state": item.metadata.get("index_cleanup_state", ""),
            "index_cleanup_error": item.metadata.get("index_cleanup_error", ""),
        }

    async def close(self):
        if self.backend:
            await self.backend.close()
