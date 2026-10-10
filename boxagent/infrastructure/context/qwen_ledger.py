"""Derive a Session Context Ledger from the exact Qwen Realtime wire traffic.

The recorder observes the same trace stream as ``logs/runtime/qwen``: every
non-audio ``client.request`` plus selected provider events. Recording from the
wire guarantees the ledger matches what the provider actually received. Turn
boundaries come from the provider (``speech_started``) or from the host through
``ContextObserver``. Any recording failure is swallowed: accounting can never
affect a conversation.
"""

import hashlib
import json
import logging
import queue
import threading
import time

from boxagent.agent.harness.instructions import PERSONA_BOUNDARY
from boxagent.domain.context.estimate import estimate_tokens

logger = logging.getLogger(__name__)


PERSONA_MARKER = PERSONA_BOUNDARY.split("{name}", 1)[0]
SYSTEM_PREFIXES = (
    ("<boxagent_user_profile>", "profile"),
    ("<boxagent_context_checkpoint>", "checkpoint"),
    ("[BoxAgent可信运行环境]", "environment"),
    ("<boxagent_memory_context>", "memory"),
)
NOTIFICATION_PREFIX = "[可信任务结果]"


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _measure(content, **extra):
    text = content if isinstance(content, str) else json.dumps(
        content, ensure_ascii=False, separators=(",", ":"))
    return {"content": text, "chars": len(text), "tokens": estimate_tokens(text),
            **extra}


class _SessionState:
    def __init__(self):
        self.restoring = False
        self.turn_open = False
        self.turn_has_user = False


class QwenContextRecorder:
    """Implements ``ContextObserver`` and the Qwen trace observer signature."""

    runtime = "qwen_realtime"

    def __init__(self, store, *, model="", log=None, clock=time.time,
                 background=False):
        self.store = store
        self.model = model
        self.log = log or (lambda *_args, **_kwargs: None)
        self.clock = clock
        self.states = {}
        self.lock = threading.Lock()
        # fsync costs milliseconds; in production the trace callback runs on
        # the Engine event loop, so a single writer thread keeps it off the
        # realtime receive path while preserving append order.
        self.pending = queue.Queue() if background else None
        if background:
            threading.Thread(target=self._drain, name="boxagent-context-ledger",
                             daemon=True).start()

    def flush(self, timeout=5.0):
        """Block until every queued record is written (tests and shutdown)."""
        if self.pending is None:
            return True
        done = threading.Event()
        self.pending.put(done)
        return done.wait(timeout)

    def _drain(self):
        while True:
            item = self.pending.get()
            if isinstance(item, threading.Event):
                item.set()
                continue
            self._write(*item)

    # ContextObserver ---------------------------------------------------------------

    def turn_started(self, session_id, *, channel):
        self._guard(session_id, "turn/start",
                    lambda state: self._start_turn(session_id, state, channel))

    def turn_prepared(self, session_id, **data):
        self._guard(session_id, "turn/prepare",
                    lambda _state: self._append(session_id, "turn/prepare", data))

    # Trace observer ----------------------------------------------------------------

    def observe(self, event, **data):
        """Trace-compatible observer: ``observe(event_name, **payload)``."""
        session_id = str(data.get("product_session_id") or "")
        self._guard(session_id, event,
                    lambda state: self._observe(session_id, state, event, data))

    # Internal ----------------------------------------------------------------------

    def _guard(self, session_id, event, action):
        if not session_id:
            return
        try:
            with self.lock:
                action(self.states.setdefault(session_id, _SessionState()))
        except Exception as exc:  # noqa: BLE001 - accounting must fail open
            self._report(event, session_id, exc)

    def _append(self, session_id, kind, data):
        record = (session_id, kind, data, self.clock())
        if self.pending is None:
            self._write(*record, raise_errors=True)
        else:
            self.pending.put(record)

    def _write(self, session_id, kind, data, at, *, raise_errors=False):
        try:
            self.store.append(session_id, kind, data, at=at)
        except Exception as exc:  # noqa: BLE001 - accounting must fail open
            if raise_errors:
                raise
            self._report(kind, session_id, exc)

    def _report(self, event, session_id, exc):
        try:
            self.log("context_ledger.record_failed", source_event=event,
                     session_id=session_id, error=repr(exc))
        except Exception:  # noqa: BLE001 - diagnostics must not raise either
            logger.warning("context ledger %s failed for %s", event, session_id,
                           exc_info=exc)

    def _start_turn(self, session_id, state, channel):
        state.restoring = False
        state.turn_open = True
        state.turn_has_user = False
        self._append(session_id, "turn/start", {"channel": channel})

    def _user(self, session_id, state, text, channel):
        if not state.turn_open or state.turn_has_user:
            self._start_turn(session_id, state, channel)
        state.turn_has_user = True
        self._append(session_id, "user/message", _measure(
            text, category="user", role="user", channel=channel))

    def _observe(self, session_id, state, event, data):
        if event == "client.request":
            self._request(session_id, state, data.get("request") or {})
        elif event == "history.injected":
            state.restoring = False
        elif event == "input_audio_buffer.speech_started":
            self._start_turn(session_id, state, "voice")
        elif event == "conversation.item.input_audio_transcription.completed":
            transcript = str(data.get("transcript") or "")
            if transcript.strip():
                self._user(session_id, state, transcript, "voice")
        elif event == "response.created":
            state.restoring = False
            self._append(session_id, "response/created", {})
        elif event in {"response.audio_transcript.done", "response.text.done"}:
            transcript = str(data.get("transcript") or "")
            if transcript.strip():
                self._append(session_id, "assistant/message", _measure(
                    transcript, category="assistant", role="assistant"))
        elif event == "response.function_call_arguments.done":
            self._append(session_id, "tool/call", _measure(
                {"name": data.get("name"), "arguments": data.get("arguments")},
                category="tool_call", role="assistant",
                name=str(data.get("name") or ""),
                call_id=str(data.get("call_id") or "")))

    def _request(self, session_id, state, request):
        kind = request.get("type")
        if kind == "session.update":
            self._session_update(session_id, state, request.get("session") or {})
        elif kind == "response.create":
            state.restoring = False
            self._append(session_id, "response/requested", {})
        elif kind == "conversation.item.create":
            self._item(session_id, state, request.get("item") or {})

    def _session_update(self, session_id, state, session):
        instructions = str(session.get("instructions") or "")
        tools = session.get("tools") or []
        state.restoring = True
        state.turn_open = False
        state.turn_has_user = False
        self._append(session_id, "context/rebuild", {
            "runtime": self.runtime, "model": self.model,
            "instructions_hash": _hash(instructions),
            "tools_hash": _hash(json.dumps(tools, ensure_ascii=False, sort_keys=True)),
            "turn_detection": (session.get("turn_detection") or {}).get("type", ""),
        })
        base, marker, persona = instructions.partition(PERSONA_MARKER)
        self._append(session_id, "system/message", _measure(
            base, category="instructions", role="system"))
        if marker:
            self._append(session_id, "system/message", _measure(
                marker + persona, category="persona", role="system"))
        self._append(session_id, "request/header", _measure(
            tools, category="tools", role="system",
            names=[(item.get("function") or item).get("name", "")
                   for item in tools if isinstance(item, dict)]))

    def _item(self, session_id, state, item):
        item_type = item.get("type")
        if item_type == "function_call_output":
            self._append(session_id, "tool/result", _measure(
                str(item.get("output") or ""), category="tool_result",
                role="tool", call_id=str(item.get("call_id") or "")))
            return
        if item_type != "message":
            return
        role = item.get("role")
        text = "".join(str(part.get("text") or "")
                       for part in item.get("content") or [] if isinstance(part, dict))
        if role == "system":
            category = next((name for prefix, name in SYSTEM_PREFIXES
                             if text.startswith(prefix)), "system_other")
            if category in {"environment", "memory"}:
                state.restoring = False
            self._append(session_id, "system/message", _measure(
                text, category=category, role="system"))
        elif state.restoring:
            self._append(session_id, f"{role}/message", _measure(
                text, category="history", role=role))
        elif role == "user" and text.startswith(NOTIFICATION_PREFIX):
            self._start_turn(session_id, state, "notification")
            state.turn_has_user = True
            self._append(session_id, "user/message", _measure(
                text, category="notification", role="user", channel="notification"))
        elif role == "user":
            self._user(session_id, state, text, "text")
        elif role == "assistant":
            self._append(session_id, "assistant/message", _measure(
                text, category="assistant", role="assistant", channel="host"))
