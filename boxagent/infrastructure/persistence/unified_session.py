"""Product and context projections backed by SessionEventStore."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from boxagent.core.ids import new_session_id
from boxagent.domain.context.models import LedgerEvent
from boxagent.domain.conversation.models import (
    ContextCheckpoint, ProductEvent, RuntimeBinding, RuntimeContextSegment,
    SessionMetadata,
)
from boxagent.infrastructure.persistence.jsonl_context_ledger import _scrub
from boxagent.infrastructure.persistence.session_events import SessionEventStore, write_json

PRODUCT_TYPES = {"interaction.started": "turn/start",
                 "interaction.finalized": "turn/end",
                 "context.checkpoint.created": "compaction/created"}


def product_kind(event: ProductEvent) -> str:
    return (f"{event.role}/message" if event.type == "message.final"
            else PRODUCT_TYPES.get(event.type, event.type))


def ledger_kind(kind: str) -> str:
    if kind in {"system/message", "user/message", "assistant/message",
                "tool/call", "tool/result", "request/header"}:
        return "context/item"
    return "context/" + kind if not kind.startswith("context/") else kind


class UnifiedContextLedger:
    def __init__(self, events: SessionEventStore) -> None:
        self.events = events

    def append(self, session_id: str, event_type: str, data: dict[str, Any], *,
               at: float | None = None) -> LedgerEvent:
        scrubbed = _scrub(data)
        if not isinstance(scrubbed, dict):
            raise ValueError("Context event 必须是 object")
        event = self.events.append(session_id, ledger_kind(event_type), {
            **scrubbed, "ledger_type": event_type}, at=at)
        return LedgerEvent(event.seq, event_type, event.time,
                           {k: v for k, v in event.data.items() if k != "ledger_type"})

    def read(self, session_id: str) -> list[LedgerEvent]:
        return [LedgerEvent(e.seq, e.data["ledger_type"], e.time,
                            {k: v for k, v in e.data.items()
                             if k not in {"ledger_type", "dedup_key"}})
                for e in self.events.read(session_id) if "ledger_type" in e.data]


class UnifiedSessionRepository:
    def __init__(self, events: SessionEventStore) -> None:
        self.events = events
        self.root = events.root
        self.index_path = self.root / "index.json"
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            write_json(self.index_path, {"schema_version": 1,
                                       "active_session_id": None, "sessions": []})

    def _index(self) -> dict[str, Any]:
        return json.loads(self.index_path.read_text(encoding="utf-8"))

    async def create_session(self, title: str = "新会话") -> SessionMetadata:
        async with self.lock:
            now = time.time()
            meta = SessionMetadata(new_session_id(), self._title(title), now, now)
            self.events.create(meta.session_id, created_at=now)
            self.events.save_state(meta.session_id, {"metadata": meta.payload(), "bindings": {}})
            index = self._index()
            index["sessions"].append(meta.session_id)
            index["active_session_id"] = meta.session_id
            write_json(self.index_path, index)
            return meta

    async def list_sessions(self, *, include_archived: bool = False) -> list[SessionMetadata]:
        sessions = [self._meta(sid) for sid in self._index()["sessions"]]
        return sorted([s for s in sessions if include_archived or not s.archived],
                      key=lambda s: s.updated_at, reverse=True)

    async def active_session(self) -> SessionMetadata | None:
        sid = self._index().get("active_session_id")
        if not sid:
            return None
        meta = self._meta(sid)
        return None if meta.archived else meta

    async def activate_session(self, session_id: str) -> SessionMetadata:
        async with self.lock:
            meta = self._meta(session_id)
            if meta.archived:
                raise ValueError("已归档 Session 不能直接激活")
            index = self._index()
            index["active_session_id"] = session_id
            write_json(self.index_path, index)
            return meta

    async def archive_session(self, session_id: str) -> SessionMetadata:
        async with self.lock:
            meta = replace(self._meta(session_id), archived=True, updated_at=time.time())
            self._save_meta(meta)
            index = self._index()
            if index["active_session_id"] == session_id:
                candidates = [sid for sid in index["sessions"]
                              if sid != session_id and not self._meta(sid).archived]
                index["active_session_id"] = candidates[-1] if candidates else None
                write_json(self.index_path, index)
            await asyncio.to_thread(self.events.compress, session_id)
            return meta

    async def rename_session(self, session_id: str, title: str) -> SessionMetadata:
        async with self.lock:
            meta = replace(self._meta(session_id), title=self._title(title), updated_at=time.time())
            self._save_meta(meta)
            return meta

    async def append_event(self, event: ProductEvent) -> ProductEvent:
        async with self.lock:
            meta = self._meta(event.session_id)
            prior = await self.read_events(event.session_id)
            duplicate = next((e for e in prior if e.event_id == event.event_id), None)
            if duplicate is not None:
                return duplicate
            stored = replace(event, sequence=prior[-1].sequence + 1 if prior else 1)
            await asyncio.to_thread(self.events.append, event.session_id, product_kind(event),
                                    {"product_event": stored.payload()},
                                    at=event.occurred_at, dedup_key="product:" + event.event_id)
            self._save_meta(replace(meta, updated_at=max(meta.updated_at, event.occurred_at)))
            return stored

    async def read_events(self, session_id: str, *, after_sequence: int = 0,
                          limit: int | None = None) -> list[ProductEvent]:
        self._meta(session_id)
        rows = await asyncio.to_thread(self.events.read, session_id)
        events = [ProductEvent.from_payload(e.data["product_event"])
                  for e in rows if "product_event" in e.data]
        events = sorted((e for e in events if e.sequence > after_sequence),
                        key=lambda e: e.sequence)
        return events[-limit:] if limit is not None else events

    async def runtime_binding(self, session_id: str, runtime: str) -> RuntimeBinding | None:
        value = self.events.state(session_id).get("bindings", {}).get(runtime)
        return RuntimeBinding.from_payload(value) if value else None

    async def save_runtime_binding(self, session_id: str, binding: RuntimeBinding) -> None:
        async with self.lock:
            state = self.events.state(session_id)
            state.setdefault("bindings", {})[binding.runtime] = binding.payload()
            self.events.save_state(session_id, state)

    async def latest_checkpoint(self, session_id: str, runtime: str) -> ContextCheckpoint | None:
        rows = await asyncio.to_thread(self.events.read, session_id, types={"compaction/checkpoint"})
        matches = [ContextCheckpoint.from_payload(e.data["payload"]) for e in rows
                   if e.data["payload"]["runtime"] == runtime]
        return matches[-1] if matches else None

    async def append_checkpoint(self, checkpoint: ContextCheckpoint,
                                segment: RuntimeContextSegment) -> ContextCheckpoint:
        self._meta(checkpoint.session_id)
        if (checkpoint.session_id, checkpoint.runtime, checkpoint.checkpoint_id,
            checkpoint.covered_through_sequence) != (
                segment.session_id, segment.runtime, segment.checkpoint_id, segment.end_sequence):
            raise ValueError("Checkpoint 与 Segment 不匹配")
        await asyncio.to_thread(self.events.append, checkpoint.session_id, "compaction/checkpoint",
                                {"payload": checkpoint.payload(), "segment": segment.payload()},
                                at=checkpoint.created_at, dedup_key="checkpoint:" + checkpoint.checkpoint_id)
        return checkpoint

    async def context_segments(self, session_id: str, runtime: str) -> list[RuntimeContextSegment]:
        rows = await asyncio.to_thread(self.events.read, session_id, types={"compaction/checkpoint"})
        return [RuntimeContextSegment.from_payload(e.data["segment"]) for e in rows
                if e.data["segment"]["runtime"] == runtime]

    def _meta(self, session_id: str) -> SessionMetadata:
        return SessionMetadata.from_payload(self.events.state(session_id)["metadata"])

    def _save_meta(self, meta: SessionMetadata) -> None:
        state = self.events.state(meta.session_id)
        state["metadata"] = meta.payload()
        self.events.save_state(meta.session_id, state)

    @staticmethod
    def _title(title: str) -> str:
        if not isinstance(title, str):
            raise ValueError("Session 标题必须是文本")
        return " ".join(title.strip().split())[:80] or "新会话"

    async def close(self) -> None:
        await asyncio.to_thread(self.events.close)
