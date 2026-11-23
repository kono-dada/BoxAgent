"""Jev-Mem-first long-term-memory domain service.

Jev-Mem owns the only long-term memory state. Product Session events remain source
evidence; job files are delivery state, not a second memory database.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TypedDict

from boxagent.domain.memory.contracts import (
    BackendRecord,
    MemoryBackend,
    MemoryJobStore,
    MemoryUnavailable,
    ProfileProjection,
)
from boxagent.domain.memory.evidence import PublicMemoryItem, public_memory_items
from boxagent.domain.memory.policy import (
    MEMORY_QUERY_LIMIT,
    MEMORY_TEXT_LIMIT,
    contains_secret,
    require_memory_ids,
    require_text,
)

logger = logging.getLogger(__name__)


class MemoryRecall(TypedDict):
    status: str
    message: str
    memories: list[PublicMemoryItem]
    evidence: object
    trace: dict[str, object]


class MemoryEvidence(TypedDict):
    profile: list[dict[str, object]]
    evidence: list[PublicMemoryItem]
    narrative: list[PublicMemoryItem]
    trace: dict[str, object]


class MemoryService:
    def __init__(
        self,
        backend: MemoryBackend | None,
        job_store: MemoryJobStore | None = None,
        projection_callback: Callable[[], object] | None = None,
        backend_recall_timeout: float = 2.0,
        profile_projector: ProfileProjection | None = None,
    ) -> None:
        self.backend = backend
        self.job_store = job_store
        self.projection_callback = projection_callback
        self.profile_projector = profile_projector
        self.backend_recall_timeout = backend_recall_timeout
        self.recalled_memory_ids: set[str] = set()
        self.recalled_memory_content: dict[str, str] = {}
        self.prewarm_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.prewarm_task is None and self.backend is not None:
            self.prewarm_task = asyncio.create_task(
                self._prewarm_backend(), name="boxagent-memory-prewarm")

    async def _prewarm_backend(self) -> None:
        try:
            await self._require_backend().health()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("memory backend prewarm failed", exc_info=True)

    def _require_backend(self) -> MemoryBackend:
        if self.backend is None:
            raise MemoryUnavailable("长期记忆未启用")
        return self.backend

    async def observe_user_message(self, content: str, *, session_id: str,
                                   interaction_id: str, source_event_id: str,
                                   occurred_at: float | None = None,
                                   channel: str | None = "text") -> BackendRecord:
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
        observation: BackendRecord = {"content": content, "metadata": metadata}
        if occurred_at:
            observation["timestamp"] = datetime.fromtimestamp(
                occurred_at, UTC).isoformat()
        result = await self._require_backend().remember([observation])
        if result.get("admitted") and self.profile_projector is not None:
            for node in [*result.get("created", []), *result.get("reused", [])]:
                await self.profile_projector.observe(node)
        await self._refresh_projection()
        return result

    async def remember(
        self,
        content: object,
        *,
        source: str = "explicit",
        session_id: str = "",
        interaction_id: str = "",
        source_event_ids: Iterable[str] = (),
    ) -> BackendRecord:
        content = require_text(content, name="content", limit=MEMORY_TEXT_LIMIT)
        if contains_secret(content):
            raise ValueError("密码、Token、API Key 或验证码不能写入长期记忆")
        result = await self._require_backend().remember([{
            "content": content,
            # Jev-Mem orders nodes by time; an untimed node cannot be compared
            # with timezone-aware ones and aborts the whole write.
            "timestamp": datetime.now(UTC).isoformat(),
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
            for node in [*result.get("created", []), *result.get("reused", [])]:
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

    async def recall(self, query: object, *, top_k: int = 5, mode: str = "deep") -> MemoryRecall:
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

    async def prepare_context(self, query: str, *, top_k: int = 5) -> MemoryEvidence:
        """L2: one bounded Jev-Mem retrieval using the raw user query."""
        try:
            result = await asyncio.wait_for(
                self.recall(query, top_k=top_k, mode="direct"),
                timeout=self.backend_recall_timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - L2 memory must degrade, not block chat
            logger.warning("memory context degraded: %s", type(exc).__name__,
                           exc_info=not isinstance(exc, TimeoutError))
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

    async def list_memories(self) -> list[BackendRecord]:
        graph = await self.snapshot(node_limit=200, edge_limit=0)
        return list(graph.get("nodes", []))

    async def snapshot(
        self, *, query: str = "", selected_id: str | None = None,
        node_limit: int = 100, edge_limit: int = 200, statuses: object = None,
    ) -> BackendRecord:
        del statuses
        return await self._require_backend().inspect(
            query=query, selected_id=selected_id,
            node_limit=node_limit, edge_limit=edge_limit)

    async def delete_node(self, memory_id: object) -> BackendRecord:
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

    async def forget(self, memory_ids: object) -> BackendRecord:
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

    async def health(self) -> BackendRecord:
        if self.backend is None:
            return {"status": "degraded", "backend": "disabled",
                    "memory_count": 0}
        try:
            backend = await self.backend.health()
        except Exception as exc:  # noqa: BLE001 - health is diagnostic
            logger.warning("memory backend health check failed", exc_info=True)
            return {"status": "degraded", "backend": type(exc).__name__,
                    "memory_count": 0}
        return {"status": "ready", "backend": backend,
                "memory_count": int(backend.get("memory_count") or 0)}

    def _remember_recalled(self, memories: Iterable[PublicMemoryItem]) -> None:
        for item in memories:
            if memory_id := item["id"]:
                self.recalled_memory_ids.add(memory_id)
                if content := item["content"]:
                    self.recalled_memory_content[memory_id] = content

    async def _refresh_projection(self) -> None:
        if self.projection_callback is None:
            return
        value = self.projection_callback()
        if asyncio.iscoroutine(value):
            await value

    async def close(self) -> None:
        if self.prewarm_task is not None and not self.prewarm_task.done():
            self.prewarm_task.cancel()
            await asyncio.gather(self.prewarm_task, return_exceptions=True)
        if self.backend:
            await self.backend.close()
