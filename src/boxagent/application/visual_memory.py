"""PersonOS-style evidence -> episode -> atoms, behind MemoryModule."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
import hashlib
import logging
from typing import Any

from boxagent.domain.memory.policy import contains_secret
from boxagent.domain.memory.visual import (
    VisualEvidence, VisualEvidenceStore, VisualEpisodeBackend,
    VisualEpisodeExtractor, VisualMemorySource, requests_visual_memory, references_past_visual_memory,
)

logger = logging.getLogger(__name__)

class VisualMemoryCoordinator:
    def __init__(self, backend: VisualEpisodeBackend, store: VisualEvidenceStore,
                 extractor: VisualEpisodeExtractor, source: VisualMemorySource, *,
                 completed: Callable[[VisualEvidence, dict[str, Any]], Awaitable[None]] | None = None) -> None:
        self.backend, self.store, self.extractor, self.source = backend, store, extractor, source
        self.completed = completed
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()
        self.closed = False

    async def start(self) -> None:
        for evidence_id, ids in await self.store.deleting():
            await self.backend.forget_episode(evidence_id)
            await self.store.finish_delete(evidence_id)
        for evidence in await self.store.pending():
            self._schedule(evidence)

    async def remember(self, *, user_request: str, session_id: str, interaction_id: str,
                       source_event_id: str, requested_at: float) -> dict[str, Any]:
        if self.closed or not requests_visual_memory(user_request) or not source_event_id or not session_id:
            return {"status": "failed", "message": "保存画面需要当前用户明确要求记住该画面。"}
        if references_past_visual_memory(user_request):
            return {"status": "clarification_required", "message": "请先打开那张原图，再说记住当前画面，我才能保存对应的来源。",
                    "speech_guidance": "只简短请用户打开原图；不能把当前画面当成过去那张图。"}
        frame = self.source.current_frame(requested_at=requested_at)
        if frame is None:
            return {"status": "clarification_required", "message": "我还没有对应的画面，请打开屏幕观察并停留在要记住的页面。",
                    "speech_guidance": "只简短说明缺少画面，不能声称已保存。"}
        if contains_secret(user_request) or contains_secret(frame.caption):
            return {"status": "failed", "message": "含密钥等敏感内容的画面描述不能写入记忆。"}
        sha = hashlib.sha256(frame.image).hexdigest()
        identity = hashlib.sha256((session_id + ":" + source_event_id + ":" + sha).encode()).hexdigest()
        evidence = VisualEvidence("visual_" + identity, session_id, interaction_id,
                                  source_event_id, user_request[:4000], frame.caption[:4000],
                                  frame.captured_at, requested_at, frame.app, frame.window_id, sha)
        evidence = await self.store.put(evidence, frame.image)
        ids = await self.store.memory_ids(evidence.evidence_id)
        if ids:
            return {"status": "succeeded", "message": "这段画面经历已经记住。", "memory_ids": ids}
        self._schedule(evidence)
        return {"status": "queued", "message": "画面已留存，正在整理这段经历。",
                "evidence_id": evidence.evidence_id,
                "speech_guidance": "用一句话确认已留存画面、仍在整理；不能提前说记忆已经整理完成。"}

    def _schedule(self, evidence: VisualEvidence) -> None:
        if self.closed or evidence.evidence_id in self.tasks:
            return
        task = asyncio.create_task(self._process(evidence), name="boxagent-visual-memory")
        self.tasks[evidence.evidence_id] = task
        task.add_done_callback(lambda _done: self.tasks.pop(evidence.evidence_id, None))

    async def _process(self, evidence: VisualEvidence) -> None:
        result: dict[str, Any] = {"status": "failed", "message": "经历整理失败，来源已留存。"}
        for delay in (0, 1, 2):
            if delay:
                await asyncio.sleep(delay)
            if self.closed or not await self.store.attempt(evidence.evidence_id):
                break
            try:
                episode = await self.store.compiled(evidence.evidence_id)
                if episode is None:
                    episode = await asyncio.wait_for(self.extractor.extract(evidence), 25)
                    await self.store.save_compiled(episode)
                # Serialize source binding and deletion; graph writes may finish remotely
                # after cancellation, so a pending receipt survives for idempotent retry.
                async with self.lock:
                    if self.closed or not await self.store.write_allowed(evidence.evidence_id):
                        return
                    response = await self.backend.remember_episode(episode.payload())
                    ids = response.get("memory_ids")
                    if not isinstance(ids, list) or len(ids) != len(episode.atoms) + 1 \
                            or any(not isinstance(i, str) or not i.strip() or len(i) > 200 for i in ids) \
                            or response.get("evidence_id") != evidence.evidence_id:
                        raise ValueError("经历存储没有确认记忆 ID")
                    await self.store.bind(evidence.evidence_id, ids)
                result = {"status": "succeeded", "message": "这段画面经历已记住。", "memory_ids": ids}
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.store.failure(evidence.evidence_id, type(exc).__name__)
        if self.completed and not self.closed:
            try:
                await self.completed(evidence, result)
            except Exception as exc:
                # A metadata notification failure cannot revoke committed memory.
                logger.warning("visual memory completion notification failed: %s", type(exc).__name__)

    async def preview(self, memory_id: str) -> dict[str, Any]:
        async with self.lock:
            evidence = await self.store.for_memory(memory_id)
            if evidence is None:
                return {"status": "not_found", "message": "这条记忆没有可回看的画面。"}
            image = await self.store.read(evidence)
            return {"status": "succeeded", "evidence_id": evidence.evidence_id,
                    "image": "data:image/png;base64," + base64.b64encode(image).decode(),
                    "caption": evidence.caption, "user_request": evidence.user_request,
                    "captured_at": evidence.captured_at, "app": evidence.app,
                    "source_event_id": evidence.source_event_id}

    async def look(self, memory_id: str, question: str) -> dict[str, Any]:
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise ValueError("原图回看问题必须是 1 至 500 字")
        async with self.lock:
            evidence = await self.store.for_memory(memory_id)
            if evidence is None:
                return {"status": "not_found", "message": "没有可回看的来源画面。"}
            image = await self.store.read(evidence)
        try:
            description = await asyncio.wait_for(self.source.describe(image, question), 30)
        except (TimeoutError, OSError, ValueError, RuntimeError):
            return {"status": "failed", "message": "暂时无法重读原图，现有文字观察仍可参考。"}
        async with self.lock:
            if await self.store.for_memory(memory_id) is None:
                return {"status": "not_found", "message": "这段经历已删除。"}
        return {"status": "succeeded", "memory_id": memory_id, "evidence_id": evidence.evidence_id,
                "observation": description[:2000], "captured_at": evidence.captured_at,
                "source_event_id": evidence.source_event_id, "kind": "visual_observation",
                "speech_guidance": "这是针对原图的观察，不能当成用户陈述；只用一两句回答，看不清就说看不清。"}

    async def forget_sources(self, memory_ids: list[str], *, evidence_ids: list[str] | None = None) -> list[str]:
        deleted: list[str] = []
        async with self.lock:
            sources = list(evidence_ids or [])
            for memory_id in memory_ids:
                evidence = await self.store.for_memory(memory_id)
                if evidence is not None:
                    sources.append(evidence.evidence_id)
            for evidence_id in dict.fromkeys(sources):
                known_ids = await self.store.memory_ids(evidence_id)
                await self.store.tombstone(evidence_id)
                result = await self.backend.forget_episode(evidence_id)
                await self.store.finish_delete(evidence_id)
                deleted.extend(known_ids)
                deleted.extend(identity for identity in result.get("memory_ids", []) if isinstance(identity, str))
        return list(dict.fromkeys(deleted))

    async def drain(self) -> None:
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks.values()))

    async def close(self) -> None:
        self.closed = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)
