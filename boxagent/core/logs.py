"""Logging primitives every layer may use (INFRA-03).

Only side-effect-free pieces live here: correlation ids carried by
``contextvars``, the JSON record format, and the asyncio error reporter that
interface loops install. Opening files, rotation, process hooks and the
``configure_logging`` entry point belong to ``bootstrap.logs``; code outside
bootstrap keeps calling ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import json
import logging
import logging.handlers
import traceback
from datetime import datetime, timezone

from boxagent.core.errors import redact

_CONTEXT_FIELDS = ("session_id", "interaction_id", "task_id")
_context = {name: contextvars.ContextVar(f"boxagent_{name}", default="")
            for name in _CONTEXT_FIELDS}


def bind_log_context(**ids):
    """Attach correlation ids to every record logged later in this context.

    ``contextvars`` follow asyncio tasks: values set inside a task are visible
    to that task and to tasks it creates afterwards, not to its siblings.
    Unknown names are rejected so a typo cannot silently drop an id.
    """
    for name, value in ids.items():
        if name not in _context:
            raise ValueError(f"未知日志上下文字段：{name}")
        _context[name].set(str(value or ""))



@contextlib.contextmanager
def log_context(**ids):
    """Temporarily bind correlation ids; restores the previous values."""
    tokens = []
    for name, value in ids.items():
        if name not in _context:
            raise ValueError(f"未知日志上下文字段：{name}")
        tokens.append((name, _context[name].set(str(value or ""))))
    try:
        yield
    finally:
        for name, token in reversed(tokens):
            _context[name].reset(token)


def current_log_context():
    return {name: var.get() for name, var in _context.items() if var.get()}


def iso_time(created):
    return datetime.fromtimestamp(created, timezone.utc).isoformat(
        timespec="milliseconds")


class JsonFormatter(logging.Formatter):
    """One redacted JSON object per record."""

    def __init__(self, process):
        super().__init__()
        self.process = process

    def format(self, record):
        entry = {
            "ts": iso_time(record.created),
            "level": record.levelname.lower(),
            "process": self.process,
            "pid": record.process,
            "thread": record.threadName,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "boxagent_context", None) or {})
        data = getattr(record, "data", None)
        if data:
            entry["data"] = data
        exc_text = record.exc_text
        if record.exc_info and not exc_text:
            exc_text = "".join(traceback.format_exception(*record.exc_info))
        if exc_text:
            entry["exc_type"] = getattr(record, "exc_type", "") or (
                record.exc_info[0].__name__ if record.exc_info else "")
            entry["exc"] = exc_text
        if record.stack_info:
            entry["stack"] = record.stack_info
        return json.dumps(redact(entry), ensure_ascii=False, default=repr,
                          separators=(",", ":"))


class EnqueueHandler(logging.handlers.QueueHandler):
    """Capture everything time- or context-dependent before crossing threads."""

    def prepare(self, record):
        record = copy.copy(record)
        record.message = record.getMessage()
        record.msg, record.args = record.message, None
        if record.exc_info:
            record.exc_type = record.exc_info[0].__name__
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_info = None
        record.boxagent_context = current_log_context()
        return record


crash_logger = logging.getLogger("boxagent.crash")


def asyncio_exception_handler(loop, context):
    """Log an asyncio loop error as one structured record.

    It replaces the loop's default handler, which would only print the same
    error a second time through the ``asyncio`` logger.
    """
    exception = context.get("exception")
    crash_logger.error(
        "asyncio: %s", context.get("message") or repr(exception),
        exc_info=(type(exception), exception, exception.__traceback__)
        if exception is not None else None,
        extra={"data": {key: repr(value) for key, value in context.items()
                        if key not in {"message", "exception"}}})


def install_asyncio_handler(loop=None):
    loop = loop or asyncio.get_running_loop()
    loop.set_exception_handler(asyncio_exception_handler)
    return loop
