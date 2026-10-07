"""Single-writer JSONL store for BoxAgent Product Sessions."""

import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

from boxagent.core.ids import new_session_id
from boxagent.domain.conversation.models import (
    ContextCheckpoint,
    ProductEvent,
    RuntimeBinding,
    RuntimeContextSegment,
    SCHEMA_VERSION,
    SessionMetadata,
)


class JsonlSessionStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.index_path = self.root / "index.json"
        self.sessions_path = self.root / "sessions"
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            self.sessions_path.mkdir(parents=True, exist_ok=True)
            if not self.index_path.exists():
                self._write_json(self.index_path, self._empty_index())
            else:
                self._read_index()

    async def create_session(self, title="新会话"):
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

    async def list_sessions(self, *, include_archived=False):
        async with self.lock:
            index = self._read_index()
            sessions = [self._read_metadata(item) for item in index["sessions"]]
            if not include_archived:
                sessions = [item for item in sessions if not item.archived]
            return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    async def active_session(self):
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

    async def activate_session(self, session_id):
        async with self.lock:
            metadata = self._read_metadata(session_id)
            if metadata.archived:
                raise ValueError("已归档 Session 不能直接激活")
            index = self._read_index()
            index["active_session_id"] = session_id
            self._write_json(self.index_path, index)
            return metadata

    async def archive_session(self, session_id):
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

    async def rename_session(self, session_id, title):
        async with self.lock:
            metadata = self._read_metadata(session_id)
            updated = replace(metadata, title=self._clean_title(title), updated_at=time.time())
            self._write_json(self._session_path(session_id) / "metadata.json", updated.payload())
            return updated

    async def append_event(self, event):
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
            self._write_json(self._session_path(event.session_id) / "metadata.json", updated.payload())
            return stored

    async def read_events(self, session_id, *, after_sequence=0, limit=None):
        async with self.lock:
            self._read_metadata(session_id)
            events = [event for event in self._read_events(
                self._session_path(session_id) / "events.jsonl", repair=True)
                      if event.sequence > after_sequence]
            return events[-limit:] if limit is not None else events

    async def runtime_binding(self, session_id, runtime):
        async with self.lock:
            bindings = self._read_bindings(session_id)
            value = bindings["bindings"].get(runtime)
            return RuntimeBinding.from_payload(value) if value else None

    async def save_runtime_binding(self, session_id, binding):
        async with self.lock:
            bindings = self._read_bindings(session_id)
            bindings["bindings"][binding.runtime] = binding.payload()
            self._write_json(
                self._session_path(session_id) / "runtime-bindings.json", bindings)

    async def latest_checkpoint(self, session_id, runtime):
        async with self.lock:
            self._read_metadata(session_id)
            checkpoints, _segments = self._read_context(
                self._session_path(session_id) / "context.jsonl", repair=True)
            matches = [item for item in checkpoints if item.runtime == runtime]
            return matches[-1] if matches else None

    async def append_checkpoint(self, checkpoint, segment):
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

    async def context_segments(self, session_id, runtime):
        async with self.lock:
            self._read_metadata(session_id)
            _checkpoints, segments = self._read_context(
                self._session_path(session_id) / "context.jsonl", repair=True)
            return [item for item in segments if item.runtime == runtime]

    def _session_path(self, session_id):
        if not isinstance(session_id, str) or not session_id.startswith("ses_") \
                or "/" in session_id or ".." in session_id:
            raise ValueError("Session ID 无效")
        return self.sessions_path / session_id

    def _read_metadata(self, session_id):
        path = self._session_path(session_id) / "metadata.json"
        return SessionMetadata.from_payload(self._read_json(path))

    def _read_bindings(self, session_id):
        self._read_metadata(session_id)
        path = self._session_path(session_id) / "runtime-bindings.json"
        if not path.exists():
            return {"schema_version": SCHEMA_VERSION, "bindings": {}}
        value = self._read_json(path)
        if value.get("schema_version") != SCHEMA_VERSION \
                or not isinstance(value.get("bindings"), dict):
            raise ValueError("Runtime Binding 文件格式无效")
        return value

    def _read_index(self):
        if not self.index_path.exists():
            return self._empty_index()
        value = self._read_json(self.index_path)
        if value.get("schema_version") != SCHEMA_VERSION \
                or not isinstance(value.get("sessions"), list):
            raise ValueError("Session index 文件格式无效")
        return value

    def _read_events(self, path, *, repair=False):
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events = []
        truncated = False
        for position, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = ProductEvent.from_payload(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                if position == len(lines) - 1:
                    truncated = True
                    break
                raise ValueError(f"Session Event Log 第 {position + 1} 行损坏")
            expected = len(events) + 1
            if event.sequence != expected:
                raise ValueError(f"Session Event sequence 不连续：期望 {expected}")
            events.append(event)
        if truncated and repair:
            self._write_lines(path, [json.dumps(event.payload(), ensure_ascii=False,
                                                separators=(",", ":")) for event in events])
        return events

    def _read_context(self, path, *, repair=False):
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
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                if position == len(lines) - 1:
                    truncated = True
                    break
                raise ValueError(f"Session Context Log 第 {position + 1} 行损坏")
            valid_lines.append(line)
        if truncated and repair:
            self._write_lines(path, valid_lines)
        return checkpoints, segments

    @staticmethod
    def _clean_title(title):
        if not isinstance(title, str):
            raise ValueError("Session 标题必须是文本")
        value = " ".join(title.strip().split())
        if not value:
            return "新会话"
        return value[:80]

    @staticmethod
    def _empty_index():
        return {"schema_version": SCHEMA_VERSION,
                "active_session_id": None, "sessions": []}

    @staticmethod
    def _read_json(path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} 必须是 JSON object")
        return value

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @classmethod
    def _write_lines(cls, path, lines):
        temporary = path.with_name(path.name + ".repair")
        with temporary.open("w", encoding="utf-8") as stream:
            for line in lines:
                stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
