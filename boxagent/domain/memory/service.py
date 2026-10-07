"""Jev-Mem-first long-term-memory domain service.

Jev-Mem owns the only long-term memory state. Product Session events remain source
evidence; job files are delivery state, not a second memory database.
"""

import asyncio
from datetime import datetime, timezone

from boxagent.domain.memory.contracts import MemoryBackend, MemoryUnavailable
from boxagent.domain.memory.evidence import public_memory_items
from boxagent.domain.memory.policy import (
    MEMORY_QUERY_LIMIT,
    MEMORY_TEXT_LIMIT,
    contains_secret,
    require_memory_ids,
    require_text,
)


class MemoryService:
    def __init__(self, backend: MemoryBackend | None, job_store=None,
                 projection_callback=None,
                 backend_recall_timeout=2.0, profile_projector=None):
        self.backend = backend
        self.job_store = job_store
        self.projection_callback = projection_callback
        self.profile_projector = profile_projector
        self.backend_recall_timeout = backend_recall_timeout
        self.recalled_memory_ids: set[str] = set()
        self.recalled_memory_content: dict[str, str] = {}
        self.prewarm_task = None

    async def start(self):
        if self.prewarm_task is None and self.backend is not None:
            self.prewarm_task = asyncio.create_task(
                self._prewarm_backend(), name="boxagent-memory-prewarm")

    async def _prewarm_backend(self):
        try:
            await self._require_backend().health()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    def _require_backend(self) -> MemoryBackend:
        if self.backend is None:
            raise MemoryUnavailable("长期记忆未启用")
        return self.backend

    async def observe_user_message(self, content, *, session_id,
                                   interaction_id, source_event_id,
                                   occurred_at=None, channel="text"):
        """Send one committed Final User Message to Jev-Mem without rewriting it."""
        content = require_text(content, name="content", limit=MEMORY_TEXT_LIMIT)
        if contains_secret(content):
            return {"admitted": 0, "rejected": 1, "reason": "secret"}
        metadata = {
            "source": "automatic",
            "role": "user",
            "session_id": session_id,
            "interaction_id": interaction_id,
            "source_event_id": source_event_id,
            "channel": channel,
        }
        observation = {"content": content, "metadata": metadata}
        if occurred_at:
            observation["timestamp"] = datetime.fromtimestamp(
                occurred_at, timezone.utc).isoformat()
        result = await self._require_backend().remember([observation])
        if result.get("admitted") and self.profile_projector is not None:
            for node in result.get("created", []):
                await self.profile_projector.observe(node)
        await self._refresh_projection()
        return result

    async def remember(self, content, *, source="explicit", session_id="",
                       interaction_id="", source_event_ids=()):
        content = require_text(content, name="content", limit=MEMORY_TEXT_LIMIT)
        if contains_secret(content):
            raise ValueError("密码、Token、API Key 或验证码不能写入长期记忆")
        result = await self._require_backend().remember([{
            "content": content,
            "metadata": {
                "source": source,
                "role": "user",
                "session_id": session_id,
                "interaction_id": interaction_id,
                "source_event_ids": list(source_event_ids),
                "explicit": True,
            },
        }])
        if result.get("admitted") and self.profile_projector is not None:
            for node in result.get("created", []):
                await self.profile_projector.observe(node)
        await self._refresh_projection()
        admitted = int(result.get("admitted") or 0)
        created = result.get("created") or result.get("reused") or []
        return {
            "status": "succeeded" if admitted else "not_stored",
            "message": "已记住。" if admitted else "这条信息未被写入长期记忆。",
            "memory_id": created[0].get("id") if created else None,
            **result,
        }

    async def recall(self, query, *, top_k=5, mode="deep"):
        query = require_text(query, name="query", limit=MEMORY_QUERY_LIMIT)
        backend = self._require_backend()
        try:
            result = await backend.query(query, top_k=top_k, mode=mode)
        except TypeError as exc:
            if "mode" not in str(exc):
                raise
            result = await backend.query(query, top_k=top_k)
        memories = public_memory_items(result.get("memories", []))
        self._remember_recalled(memories)
        return {
            "status": "succeeded",
            "message": "找到了相关记忆。" if memories else "没有找到相关记忆。",
            "memories": memories,
            "evidence": result.get("evidence", ""),
            "trace": result.get("trace", {}),
        }

    async def prepare_context(self, query, *, top_k=5):
        """L2: one bounded Jev-Mem retrieval using the raw user query."""
        try:
            result = await asyncio.wait_for(
                self.recall(query, top_k=top_k, mode="direct"),
                timeout=self.backend_recall_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - L2 memory must degrade, not block chat
            return {"profile": [], "evidence": [], "narrative": [],
                    "trace": {"degraded": True,
                              "reason": type(exc).__name__}}
        narrative = [item for item in result["memories"]
                     if item.get("type") == "NARRATIVE"]
        evidence = [item for item in result["memories"]
                    if item.get("type") != "NARRATIVE"]
        return {
            "profile": [],
            "evidence": evidence,
            "narrative": narrative,
            "trace": {**result.get("trace", {}), "degraded": False},
        }

    async def list_memories(self):
        graph = await self.snapshot(node_limit=200, edge_limit=0)
        return list(graph.get("nodes", []))

    async def snapshot(self, *, query="", selected_id=None,
                       node_limit=100, edge_limit=200, statuses=None):
        del statuses
        return await self._require_backend().inspect(
            query=query, selected_id=selected_id,
            node_limit=node_limit, edge_limit=edge_limit)

    async def delete_node(self, memory_id):
        memory_id = require_text(memory_id, name="memory_id", limit=200)
        result = await self._require_backend().forget([memory_id])
        self.recalled_memory_ids.discard(memory_id)
        self.recalled_memory_content.pop(memory_id, None)
        if self.profile_projector is not None:
            await self.profile_projector.forget(memory_id)
        await self._refresh_projection()
        deleted = public_memory_items(result.get("deleted", []))
        return {"status": "succeeded" if deleted else "not_found",
                "deleted": deleted, "missing": result.get("missing", []),
                "memory_count": result.get("memory_count")}

    async def forget(self, memory_ids):
        memory_ids = require_memory_ids(memory_ids)
        unseen = [item for item in memory_ids
                  if item not in self.recalled_memory_ids]
        if unseen:
            raise ValueError("删除前必须先检索并使用 recall_memory 返回的精确 ID")
        result = await self._require_backend().forget(memory_ids)
        deleted = public_memory_items(result.get("deleted", []))
        self.recalled_memory_ids.difference_update(memory_ids)
        for memory_id in memory_ids:
            self.recalled_memory_content.pop(memory_id, None)
            if self.profile_projector is not None:
                await self.profile_projector.forget(memory_id)
        await self._refresh_projection()
        return {"status": "succeeded" if deleted else "not_found",
                "message": f"已删除 {len(deleted)} 条记忆。" if deleted
                else "没有找到可删除的记忆。",
                "deleted": deleted, "missing": result.get("missing", []),
                "memory_count": result.get("memory_count")}

    async def health(self):
        if self.backend is None:
            return {"status": "degraded", "backend": "disabled",
                    "memory_count": 0}
        try:
            backend = await self.backend.health()
        except Exception as exc:  # noqa: BLE001 - health is diagnostic
            return {"status": "degraded", "backend": type(exc).__name__,
                    "memory_count": 0}
        return {"status": "ready", "backend": backend,
                "memory_count": int(backend.get("memory_count") or 0)}

    def _remember_recalled(self, memories):
        self.recalled_memory_ids.update(
            item["id"] for item in memories if item.get("id"))
        self.recalled_memory_content.update(
            {item["id"]: item["content"] for item in memories
             if item.get("id") and item.get("content")})

    async def _refresh_projection(self):
        if self.projection_callback is None:
            return
        value = self.projection_callback()
        if asyncio.iscoroutine(value):
            await value

    async def close(self):
        if self.prewarm_task is not None and not self.prewarm_task.done():
            self.prewarm_task.cancel()
            await asyncio.gather(self.prewarm_task, return_exceptions=True)
        if self.backend:
            await self.backend.close()
