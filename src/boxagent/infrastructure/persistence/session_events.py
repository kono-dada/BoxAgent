"""One durable, locked event stream per Product Session.

The stream sequence orders all facts. Legacy product_sequence remains a
separate cursor so migration does not silently move existing runtime bindings.
"""

from __future__ import annotations

from boxagent.core import file_lock as fcntl
import json
import gzip
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

SESSION_ID = re.compile(r"^ses_[A-Za-z0-9]{1,64}$")


@dataclass(frozen=True)
class SessionEvent:
    seq: int
    type: str
    time: float
    data: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {"seq": self.seq, "type": self.type,
                "time": self.time, "data": self.data}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name == "nt":
        return  # Windows cannot open directories with the POSIX fsync contract.
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SessionEventStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.lock = threading.RLock()
        self.leases: dict[str, TextIO] = {}
        self.cache: dict[str, tuple[tuple[int, int], list[SessionEvent]]] = {}

    def directory(self, session_id: str) -> Path:
        if not SESSION_ID.fullmatch(session_id):
            raise ValueError("无效的 Session ID")
        return self.root / session_id

    def acquire(self, session_id: str) -> None:
        with self.lock:
            if session_id in self.leases:
                return
            directory = self.directory(session_id)
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = (directory / "session.lock").open("a+")
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                stream.close()
                raise RuntimeError(f"Session 正在被另一个写入者使用：{session_id}") from exc
            self.leases[session_id] = stream

    def create(self, session_id: str, *, created_at: float) -> None:
        if not math.isfinite(created_at):
            raise ValueError("Session created_at 必须是有限值")
        with self.lock:
            self.acquire(session_id)
            path = self.directory(session_id) / "session.v1.jsonl"
            compressed = path.with_suffix(path.suffix + ".gz")
            if not path.exists() and compressed.exists():
                with gzip.open(compressed, "rb") as source:
                    raw = source.read()
                temporary = path.with_suffix(".restore")
                temporary.write_bytes(raw)
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            if path.exists():
                self._read(session_id, repair=True)
                return
            write_json(path, {"type": "session", "version": 1,
                              "session_id": session_id, "created_at": created_at})

    def append(self, session_id: str, kind: str, data: dict[str, Any], *,
               at: float | None = None, dedup_key: str = "") -> SessionEvent:
        if not isinstance(kind, str) or not kind.strip() or not isinstance(data, dict):
            raise ValueError("Session 事件必须有 type 和 object data")
        if at is not None and (isinstance(at, bool) or not math.isfinite(at)):
            raise ValueError("Session time 必须是有限值")
        # Validate before changing either the file or its in-memory cache.
        json.dumps(data, allow_nan=False)
        with self.lock:
            self.acquire(session_id)
            self.create(session_id, created_at=at if at is not None else time.time())
            rows = self._read(session_id, repair=True)
            if dedup_key:
                existing = next((e for e in rows if e.data.get("dedup_key") == dedup_key), None)
                if existing is not None:
                    return existing
            value = dict(data)
            if dedup_key:
                value["dedup_key"] = dedup_key
            event = SessionEvent(len(rows) + 1, kind,
                                 time.time() if at is None else at, value)
            path = self.directory(session_id) / "session.v1.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event.payload(), ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            stat = path.stat()
            self.cache[session_id] = ((stat.st_mtime_ns, stat.st_size), [*rows, event])
            return event

    def read(self, session_id: str, *, types: set[str] | None = None,
             after_seq: int = 0) -> list[SessionEvent]:
        with self.lock:
            return [e for e in self._read(session_id, repair=False)
                    if e.seq > after_seq and (types is None or e.type in types)]

    def _read(self, session_id: str, *, repair: bool) -> list[SessionEvent]:
        path = self.directory(session_id) / "session.v1.jsonl"
        if not path.exists():
            compressed = path.with_suffix(path.suffix + ".gz")
            if not compressed.exists():
                return []
            with gzip.open(compressed, "rt", encoding="utf-8") as stream:
                raw = stream.read()
        else:
            stat = path.stat()
            cached = self.cache.get(session_id)
            if cached and cached[0] == (stat.st_mtime_ns, stat.st_size):
                return list(cached[1])
            raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        header = json.loads(lines[0]) if lines else {}
        if not isinstance(header, dict) or header.get("type") != "session" or header.get("version") != 1 \
                or header.get("session_id") != session_id:
            raise ValueError("Session 事件流表头无效")
        events: list[SessionEvent] = []
        torn = False
        for position, line in enumerate(lines[1:], 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                if position != len(lines) - 1 or raw.endswith("\n"):
                    raise ValueError(f"Session 事件流第 {position + 1} 行损坏") from exc
                torn = True
                break
            if not isinstance(value, dict) or type(value.get("seq")) is not int \
                    or value.get("seq") != len(events) + 1 \
                    or not isinstance(value.get("type"), str) or not value["type"].strip() \
                    or not isinstance(value.get("data"), dict):
                raise ValueError(f"Session 事件流第 {position + 1} 行格式或 seq 无效")
            if isinstance(value.get("time"), bool) or not isinstance(value.get("time"), (int, float)):
                raise ValueError("Session time 必须是数值")
            events.append(SessionEvent(value["seq"], value["type"],
                                       float(value["time"]), value["data"]))
            if not math.isfinite(events[-1].time):
                raise ValueError("Session time 必须是有限值")
            json.dumps(value["data"], allow_nan=False)
        if repair and (torn or not raw.endswith("\n")):
            temporary = path.with_name(path.name + ".repair")
            with temporary.open("w", encoding="utf-8") as stream:
                for row in [header, *(e.payload() for e in events)]:
                    stream.write(json.dumps(row, ensure_ascii=False,
                                            separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        if path.exists() and not torn and (raw.endswith("\n") or repair):
            stat = path.stat()
            self.cache[session_id] = ((stat.st_mtime_ns, stat.st_size), events)
        return events

    def compress(self, session_id: str) -> None:
        with self.lock:
            self.acquire(session_id)
            if not self.state(session_id)["metadata"].get("archived"):
                raise ValueError("只能压缩已归档会话")
            self._read(session_id, repair=False)
            path = self.directory(session_id) / "session.v1.jsonl"
            if not path.exists():
                return
            raw = path.read_bytes()
            compressed = path.with_suffix(path.suffix + ".gz")
            temporary = compressed.with_suffix(".tmp")
            with gzip.open(temporary, "wb") as stream:
                stream.write(raw)
            with gzip.open(temporary, "rb") as stream:
                if stream.read() != raw:
                    raise ValueError("归档校验失败")
            os.chmod(temporary, 0o600)
            temporary.replace(compressed)
            path.unlink()
            self.cache.pop(session_id, None)

    def state(self, session_id: str) -> dict[str, Any]:
        with self.lock:
            value = json.loads((self.directory(session_id) / "state.json").read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Session state 必须是 object")
            return value

    def save_state(self, session_id: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.acquire(session_id)
            write_json(self.directory(session_id) / "state.json", value)

    def close(self) -> None:
        with self.lock:
            for stream in self.leases.values():
                fcntl.flock(stream, fcntl.LOCK_UN)
                stream.close()
            self.leases.clear()
