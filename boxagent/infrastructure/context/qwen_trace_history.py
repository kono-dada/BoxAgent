"""Read-only Context Ledger reconstruction from the global Qwen wire trace.

Sessions recorded before the Context Ledger existed have no ``context.v1.jsonl``.
Their model-visible context can still be reconstructed from
``logs/runtime/qwen/events.jsonl`` by replaying it through the production
``QwenContextRecorder`` into memory. Nothing is written to disk.
"""

import json
from collections import defaultdict
from pathlib import Path

from boxagent.domain.context.models import LedgerEvent


MEMORY_PREFIX = "<boxagent_memory_context>"


class MemoryLedgerStore:
    def __init__(self):
        self.events = defaultdict(list)

    def append(self, session_id, event_type, data, *, at=None):
        event = LedgerEvent(seq=len(self.events[session_id]) + 1, type=event_type,
                            time=float(at or 0), data=dict(data or {}))
        self.events[session_id].append(event)
        return event

    def read(self, session_id):
        return list(self.events.get(session_id, ()))


def _rows(path, session_id, key):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if session_id not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get(key) == session_id:
                rows.append(row)
    return rows


def replay_session(trace_path, session_id, recorder_factory, *, memory_log_path=None):
    """Return reconstructed ``LedgerEvent`` objects for one Session.

    ``recorder_factory(store, clock)`` builds the production recorder; it is
    injected by bootstrap so this module never constructs it directly.
    """
    clock = {"now": 0.0}
    store = MemoryLedgerStore()
    recorder = recorder_factory(store, lambda: clock["now"])
    recalls = [row for row in _rows(memory_log_path, session_id, "session_id")
               if row.get("event") == "memory_recall_finished"] if memory_log_path else []
    # The live host emits ``turn_started`` for text turns before recall; the
    # wire trace does not, so infer it: a memory item that does not follow a
    # voice transcript in the same turn opens a new text turn.
    voice = {"open": False, "user": False, "memory": False}
    for row in _rows(trace_path, session_id, "product_session_id"):
        clock["now"] = float(row.get("occurred_at") or 0)
        event = row.pop("event", "")
        if event == "input_audio_buffer.speech_started":
            voice.update(open=True, user=False, memory=False)
        elif event == "conversation.item.input_audio_transcription.completed":
            voice["user"] = True
        elif event == "client.request":
            item = (row.get("request") or {}).get("item") or {}
            text = "".join(str(part.get("text") or "")
                           for part in item.get("content") or [] if isinstance(part, dict))
            if item.get("role") == "system" and text.startswith(MEMORY_PREFIX):
                if voice["open"] and voice["user"] and not voice["memory"]:
                    voice["memory"] = True
                else:
                    voice["open"] = False
                    recorder.turn_started(session_id, channel="text")
        elif event == "memory_context.injected":
            earlier = [item for item in recalls
                       if item.get("occurred_at", 0) <= clock["now"] + 0.01]
            recall = earlier[-1] if earlier else {}
            recorder.turn_prepared(session_id, memory={
                "status": "injected", "ms": recall.get("elapsed_ms"),
                "chars": row.get("character_count", 0)})
            continue
        recorder.observe(event, **row)
    return store.read(session_id)


class QwenTraceHistory:
    """Application-facing read-only source for pre-ledger Sessions."""

    def __init__(self, trace_path, recorder_factory, *, memory_log_path=None):
        self.trace_path = Path(trace_path)
        self.recorder_factory = recorder_factory
        self.memory_log_path = memory_log_path

    def read(self, session_id):
        return replay_session(self.trace_path, session_id, self.recorder_factory,
                              memory_log_path=self.memory_log_path)
