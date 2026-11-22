"""Single-writer JSONL store for BoxAgent Product Sessions."""

import asyncio
import json
import os
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

from boxagent.core.ids import new_session_id
from boxagent.domain.conversation.models import (
    SCHEMA_VERSION,
    ContextCheckpoint,
    ProductEvent,
    RuntimeBinding,
    RuntimeContextSegment,
    SessionMetadata,
)


class _SessionIndex(TypedDict):
    schema_version: int
    active_session_id: NotRequired[str | None]
    sessions: list[str]


class _RuntimeBindings(TypedDict):
    schema_version: int
    bindings: dict[str, dict[str, Any]]


class JsonlSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.sessions_path = self.root / "sessions"
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        async with self.lock:
            self.sessions_path.mkdir(parents=True, exist_ok=True)
            if not self.index_path.exists():
                self._write_json(self.index_path, self._empty_index())
            else:
                self._read_index()

    async def create_session(self, title: str = "新会话") -> SessionMetadata:
        async with self.lock:
            self.sessions_path.mkdir(parents=True, exist_ok=True)
            index = self._read_index()
            now = time.time()
            metadata = SessionMetadata(
                session_id=new_session_id(), title=self._clean_title(title),
                created_at=now, updated_at=now)
            directory = self._session_path(metadata.session_id)
            directory.mkdir(parents=False, exist_ok=False)
            self._write_json(directory / "metadata.json", metadata.payload())
            self._write_json(directory / "runtime-bindings.json", {
                "schema_version": SCHEMA_VERSION, "bindings": {}})
            (directory / "events.jsonl").touch(mode=0o600)
            (directory / "context.jsonl").touch(mode=0o600)
            index["sessions"].append(metadata.session_id)
            index["active_session_id"] = metadata.session_id
            self._write_json(self.index_path, index)
            return metadata

    async def list_sessions(self, *, include_archived: bool = False) -> list[SessionMetadata]:
        async with self.lock:
            index = self._read_index()
            sessions = [self._read_metadata(item) for item in index["sessions"]]
            if not include_archived:
                sessions = [item for item in sessions if not item.archived]
            return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    async def active_session(self) -> SessionMetadata | None:
        async with self.lock:
            index = self._read_index()
            session_id = index.get("active_session_id")
            if not session_id:
                return None
            try:
                metadata = self._read_metadata(session_id)
            except FileNotFoundError:
                index["active_session_id"] = None
                self._write_json(self.index_path, index)
                return None
            return None if metadata.archived else metadata

    async def activate_session(self, session_id: str) -> SessionMetadata:
        async with self.lock:
            metadata = self._read_metadata(session_id)
            if metadata.archived:
                raise ValueError("已归档 Session 不能直接激活")
            index = self._read_index()
            index["active_session_id"] = session_id
            self._write_json(self.index_path, index)
            return metadata

    async def archive_session(self, session_id: str) -> SessionMetadata:
        async with self.lock:
            metadata = self._read_metadata(session_id)
            archived = replace(metadata, archived=True, updated_at=time.time())
            self._write_json(self._session_path(session_id) / "metadata.json", archived.payload())
            index = self._read_index()
            if index.get("active_session_id") == session_id:
                candidates = [item for item in index["sessions"] if item != session_id
                              and not self._read_metadata(item).archived]
                index["active_session_id"] = candidates[-1] if candidates else None
                self._write_json(self.index_path, index)
            return archived

    async def rename_session(self, session_id: str, title: str) -> SessionMetadata:
        async with self.lock:
            metadata = self._read_metadata(session_id)
            updated = replace(metadata, title=self._clean_title(title), updated_at=time.time())
            self._write_json(self._session_path(session_id) / "metadata.json", updated.payload())
            return updated

    async def append_event(self, event: ProductEvent) -> ProductEvent:
        async with self.lock:
            metadata = self._read_metadata(event.session_id)
            path = self._session_path(event.session_id) / "events.jsonl"
            events = self._read_events(path, repair=True)
            duplicate = next((item for item in events if item.event_id == event.event_id), None)
            if duplicate:
                return duplicate
            stored = replace(event, sequence=events[-1].sequence + 1 if events else 1)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(stored.payload(), ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            updated = replace(metadata, updated_at=max(metadata.updated_at, stored.occurred_at))
            self._write_json(
                self._session_path(event.session_id) / "metadata.json", updated.payload()
            )
            return stored

    async def read_events(
        self, session_id: str, *, after_sequence: int = 0, limit: int | None = None
    ) -> list[ProductEvent]:
        async with self.lock:
            self._read_metadata(session_id)
            events = [event for event in self._read_events(
                self._session_path(session_id) / "events.jsonl", repair=True)
                      if event.sequence > after_sequence]
            return events[-limit:] if limit is not None else events

    async def runtime_binding(self, session_id: str, runtime: str) -> RuntimeBinding | None:
        async with self.lock:
            bindings = self._read_bindings(session_id)
            value = bindings["bindings"].get(runtime)
            return RuntimeBinding.from_payload(value) if value else None

    async def save_runtime_binding(self, session_id: str, binding: RuntimeBinding) -> None:
        async with self.lock:
            bindings = self._read_bindings(session_id)
            bindings["bindings"][binding.runtime] = binding.payload()
            self._write_json(
                self._session_path(session_id) / "runtime-bindings.json", bindings)

    async def latest_checkpoint(self, session_id: str, runtime: str) -> ContextCheckpoint | None:
        async with self.lock:
            self._read_metadata(session_id)
            checkpoints, _segments = self._read_context(
                self._session_path(session_id) / "context.jsonl", repair=True)
            matches = [item for item in checkpoints if item.runtime == runtime]
            return matches[-1] if matches else None

    async def append_checkpoint(
        self, checkpoint: ContextCheckpoint, segment: RuntimeContextSegment
    ) -> ContextCheckpoint:
        if checkpoint.session_id != segment.session_id:
            raise ValueError("Checkpoint 与 Segment 必须属于同一 Session")
        if checkpoint.runtime != segment.runtime:
            raise ValueError("Checkpoint 与 Segment 必须属于同一 Runtime")
        if checkpoint.checkpoint_id != segment.checkpoint_id:
            raise ValueError("Checkpoint 与 Segment 引用不一致")
        if checkpoint.covered_through_sequence != segment.end_sequence:
            raise ValueError("Checkpoint cursor 与 Segment 终点不一致")
        async with self.lock:
            self._read_metadata(checkpoint.session_id)
            path = self._session_path(checkpoint.session_id) / "context.jsonl"
            checkpoints, _segments = self._read_context(path, repair=True)
            duplicate = next((item for item in checkpoints
                              if item.checkpoint_id == checkpoint.checkpoint_id), None)
            if duplicate:
                return duplicate
            records = (
                {"record_type": "segment", "payload": segment.payload()},
                {"record_type": "checkpoint", "payload": checkpoint.payload()},
            )
            with path.open("a", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record, ensure_ascii=False,
                                            separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return checkpoint

    async def context_segments(
        self, session_id: str, runtime: str
    ) -> list[RuntimeContextSegment]:
        async with self.lock:
            self._read_metadata(session_id)
            _checkpoints, segments = self._read_context(
                self._session_path(session_id) / "context.jsonl", repair=True)
            return [item for item in segments if item.runtime == runtime]

    def _session_path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not session_id.startswith("ses_") \
                or "/" in session_id or ".." in session_id:
            raise ValueError("Session ID 无效")
        return self.sessions_path / session_id

    def _read_metadata(self, session_id: str) -> SessionMetadata:
        path = self._session_path(session_id) / "metadata.json"
        return SessionMetadata.from_payload(self._read_json(path))

    def _read_bindings(self, session_id: str) -> _RuntimeBindings:
        self._read_metadata(session_id)
        path = self._session_path(session_id) / "runtime-bindings.json"
        if not path.exists():
            return {"schema_version": SCHEMA_VERSION, "bindings": {}}
        value = self._read_json(path)
        bindings = value.get("bindings")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or not isinstance(bindings, dict)
            or any(not isinstance(binding, dict) for binding in bindings.values())
        ):
            raise ValueError("Runtime Binding 文件格式无效")
        return cast(_RuntimeBindings, value)

    def _read_index(self) -> _SessionIndex:
        if not self.index_path.exists():
            return self._empty_index()
        value = self._read_json(self.index_path)
        sessions = value.get("sessions")
        active = value.get("active_session_id")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or not isinstance(sessions, list)
            or any(not isinstance(item, str) for item in sessions)
            or (active is not None and not isinstance(active, str))
        ):
            raise ValueError("Session index 文件格式无效")
        return cast(_SessionIndex, value)

    def _read_events(self, path: Path, *, repair: bool = False) -> list[ProductEvent]:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events: list[ProductEvent] = []
        truncated = False
        for position, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                value: object = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Product Event 必须是 JSON object")
                event = ProductEvent.from_payload(value)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if position == len(lines) - 1:
                    truncated = True
                    break
                raise ValueError(f"Session Event Log 第 {position + 1} 行损坏") from exc
            expected = len(events) + 1
            if event.sequence != expected:
                raise ValueError(f"Session Event sequence 不连续：期望 {expected}")
            events.append(event)
        if truncated and repair:
            self._write_lines(path, [json.dumps(event.payload(), ensure_ascii=False,
                                                separators=(",", ":")) for event in events])
        return events

    def _read_context(
        self, path: Path, *, repair: bool = False
    ) -> tuple[list[ContextCheckpoint], list[RuntimeContextSegment]]:
        if not path.exists():
            return [], []
        lines = path.read_text(encoding="utf-8").splitlines()
        checkpoints, segments, valid_lines = [], [], []
        truncated = False
        for position, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
                    raise ValueError("Context record 必须是 object")
                if record.get("record_type") == "checkpoint":
                    checkpoints.append(ContextCheckpoint.from_payload(record["payload"]))
                elif record.get("record_type") == "segment":
                    segments.append(RuntimeContextSegment.from_payload(record["payload"]))
                else:
                    raise ValueError("未知的 Context record 类型")
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if position == len(lines) - 1:
                    truncated = True
                    break
                raise ValueError(f"Session Context Log 第 {position + 1} 行损坏") from exc
            valid_lines.append(line)
        if truncated and repair:
            self._write_lines(path, valid_lines)
        return checkpoints, segments

    @staticmethod
    def _clean_title(title: str) -> str:
        if not isinstance(title, str):
            raise ValueError("Session 标题必须是文本")
        value = " ".join(title.strip().split())
        if not value:
            return "新会话"
        return value[:80]

    @staticmethod
    def _empty_index() -> _SessionIndex:
        return {"schema_version": SCHEMA_VERSION,
                "active_session_id": None, "sessions": []}

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} 必须是 JSON object")
        return value

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @classmethod
    def _write_lines(cls, path: Path, lines: Iterable[str]) -> None:
        temporary = path.with_name(path.name + ".repair")
        with temporary.open("w", encoding="utf-8") as stream:
            for line in lines:
                stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
