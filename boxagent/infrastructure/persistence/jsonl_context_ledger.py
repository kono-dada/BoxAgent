"""Append-only JSONL persistence for per-Session Context Ledgers.

Layout: ``<conversations>/sessions/<session_id>/context.v1.jsonl``. The first
line is a version header; every following line is one ``LedgerEvent``. Each
append is flushed and fsynced. A torn final line is dropped and repaired on the
next append; corruption before the final line is rejected.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

from boxagent.core.errors import redact
from boxagent.domain.context.models import LEDGER_VERSION, LedgerEvent


SESSION_ID = re.compile(r"^ses_[A-Za-z0-9]{1,64}$")
FILE_NAME = f"context.v{LEDGER_VERSION}.jsonl"


def _scrub(value):
    """Redact secrets inside strings while keeping accounting keys such as ``tokens``."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


class JsonlContextLedgerStore:
    def __init__(self, conversations_root, *, clock=time.time):
        self.root = Path(conversations_root)
        self.clock = clock
        self.lock = threading.Lock()
        self.next_seq = {}

    def path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not SESSION_ID.match(session_id):
            raise ValueError("无效的 Session ID")
        return self.root / "sessions" / session_id / FILE_NAME

    def append(self, session_id, event_type, data, *, at=None):
        path = self.path(session_id)
        with self.lock:
            if session_id not in self.next_seq:
                events = self._read(path, repair=True)
                self.next_seq[session_id] = (events[-1].seq if events else 0) + 1
            event = LedgerEvent(seq=self.next_seq[session_id], type=str(event_type),
                                time=self.clock() if at is None else float(at),
                                data=_scrub(dict(data or {})))
            path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not path.exists() or path.stat().st_size == 0
            with path.open("a", encoding="utf-8") as stream:
                if fresh:
                    stream.write(json.dumps({
                        "type": "ledger", "version": LEDGER_VERSION,
                        "session_id": session_id, "created_at": event.time,
                    }, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.write(json.dumps(event.payload(), ensure_ascii=False,
                                        separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.next_seq[session_id] = event.seq + 1
            return event

    def read(self, session_id):
        path = self.path(session_id)
        with self.lock:
            return self._read(path, repair=False)

    def forget(self, session_id):
        """Drop cached sequence state, e.g. after the Session directory is removed."""
        with self.lock:
            self.next_seq.pop(session_id, None)

    @staticmethod
    def _read(path, *, repair):
        if not path.exists():
            return []
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        events, valid, torn = [], [], False
        for position, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if position == 0:
                    if value.get("type") != "ledger" or value.get("version") != LEDGER_VERSION:
                        raise ValueError("不支持的 Context Ledger 版本")
                else:
                    event = LedgerEvent.from_payload(value)
                    if events and event.seq <= events[-1].seq:
                        raise ValueError("Context Ledger seq 不递增")
                    events.append(event)
            except ValueError as exc:
                is_last = position == len(lines) - 1
                if is_last and isinstance(exc, json.JSONDecodeError):
                    torn = True
                    break
                raise ValueError(f"Context Ledger 第 {position + 1} 行损坏：{exc}") from exc
            valid.append(line)
        if not torn and raw and not raw.endswith("\n"):
            # A complete JSON object without its newline is still a torn write:
            # rewrite with the terminator before appending more records.
            torn = True
        if torn and repair:
            temporary = path.with_name(path.name + ".repair")
            with temporary.open("w", encoding="utf-8") as stream:
                for line in valid:
                    stream.write(line + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        return events
