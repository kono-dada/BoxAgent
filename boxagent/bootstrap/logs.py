"""Process-wide diagnostic logging setup (INFRA-03).

Every BoxAgent process calls :func:`configure_logging` once at its entry point.
After that, ordinary ``logging.getLogger(__name__)`` calls anywhere in the
package produce one JSON object per line:

``<log_dir>/<process>.jsonl``
    Everything at INFO and above from this process. Size-rotated.
``<log_dir>/errors.jsonl``
    WARNING and above from every process, plus one ``logging.ready`` marker per
    launch. A log without the marker predates this module, so the absence of
    errors there proves nothing. Rate-limited so a warning inside a loop cannot
    flood the disk; the number of dropped records is reported.
``<log_dir>/crash/<process>.log``
    ``faulthandler`` output for native crashes and hard faults that never
    reach Python's exception machinery.

Diagnostics are not business records. Session events, task runs and the Qwen
wire trace keep their own files; this module never writes those.

Writes go through a ``QueueHandler``: the caller only enqueues, a background
thread formats and writes. A logging failure must never break the caller, so
handler errors are swallowed (``logging.raiseExceptions = False``) and an
unusable log directory falls back to stderr.
"""

from __future__ import annotations

import collections
import contextlib
import faulthandler
import json
import logging
import logging.handlers
import os
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

from boxagent.core.logs import (  # noqa: F401 - re-exported for entry points
    EnqueueHandler,
    JsonFormatter,
    crash_logger,
    install_asyncio_handler,
    iso_time,
)

ROOT_LOGGER = "boxagent"
READY_EVENT = "logging.ready"
PROCESS_MAX_BYTES = 10 * 1024 * 1024
PROCESS_BACKUPS = 5
ERRORS_MAX_BYTES = 10 * 1024 * 1024
ERRORS_BACKUPS = 3
RETENTION_DAYS = 14
WINDOW_SECONDS = 60.0
WINDOW_LIMIT = 100
RECENT_LIMIT = 200
_state = None
_state_lock = threading.Lock()


class RateLimitedHandler(logging.Handler):
    """Forward at most ``limit`` records per window to ``target``.

    The first record of the next window is preceded by a summary of how many
    were dropped, so suppression is visible instead of silent.
    """

    def __init__(self, target, *, limit=WINDOW_LIMIT, window=WINDOW_SECONDS,
                 clock=time.monotonic, accept=None):
        super().__init__()
        self.target = target
        self.limit, self.window, self.clock = limit, window, clock
        self.accept = accept or (lambda record: True)
        self.window_start = clock()
        self.written = 0
        self.dropped = 0

    def emit(self, record):
        if not self.accept(record):
            return
        now = self.clock()
        if now - self.window_start >= self.window:
            if self.dropped:
                summary = logging.LogRecord(
                    "boxagent.logging", logging.WARNING, __file__, 0,
                    "dropped %d message(s) over the rate limit", (self.dropped,),
                    None)
                summary.boxagent_context = {}
                summary.data = {"dropped": self.dropped,
                                "window_seconds": self.window}
                self.target.handle(summary)
            self.window_start, self.written, self.dropped = now, 0, 0
        if self.written >= self.limit:
            self.dropped += 1
            return
        self.written += 1
        self.target.handle(record)

    def close(self):
        self.target.close()
        super().close()


class RecentProblems(logging.Handler):
    """In-memory ring of recent WARNING+ records for an admin view."""

    def __init__(self, formatter, capacity=RECENT_LIMIT):
        super().__init__(logging.WARNING)
        self.setFormatter(formatter)
        self.items = collections.deque(maxlen=capacity)

    def emit(self, record):
        self.items.append(json.loads(self.format(record)))


def _is_problem_or_marker(record):
    return record.levelno >= logging.WARNING or record.getMessage() == READY_EVENT


def rotate_if_large(path, *, max_bytes, backups):
    """Rotate ``path`` to ``path.1``… when it exceeds ``max_bytes``.

    Used at startup for append-only files written outside ``logging`` (event
    streams, wire traces) so they stay bounded across launches.
    """
    path = Path(path)
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return False
        for index in range(backups - 1, 0, -1):
            source = path.with_name(f"{path.name}.{index}")
            if source.exists():
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
        path.replace(path.with_name(f"{path.name}.1"))
        return True
    except OSError:
        return False


STREAM_FILES = ("engine.log", "events.jsonl", "context-worker.log",
                "memory/worker.log", "context/ledger.jsonl",
                "context/checkpoints.jsonl", "memory/events.jsonl")
STREAM_MAX_BYTES = 20 * 1024 * 1024
STREAM_BACKUPS = 3


def rotate_stream_logs(log_dir):
    """Bound the raw stream files; returns the paths that were rotated."""
    log_dir = Path(log_dir)
    return [log_dir / name for name in STREAM_FILES
            if rotate_if_large(log_dir / name, max_bytes=STREAM_MAX_BYTES,
                               backups=STREAM_BACKUPS)]


def prune_old_logs(log_dir, *, days=RETENTION_DAYS, now=None):
    """Delete rotated backups and crash files older than ``days``."""
    log_dir = Path(log_dir)
    cutoff = (now or time.time()) - days * 86400
    removed = []
    candidates = [*log_dir.rglob("*.jsonl.[0-9]*"), *log_dir.rglob("*.log.[0-9]*"),
                  *log_dir.glob("crash/*.log.[0-9]*")]
    for path in set(candidates):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except OSError:
            continue
    return removed


def migrate_legacy_logs(data_dir, log_dir):
    """Move pre-``logs/`` diagnostic files out of the data root, once.

    Early builds wrote ``engine.log``, ``events.jsonl`` and worker logs straight
    into the data directory; current builds write them under ``logs/``. Old
    copies are moved to ``logs/legacy/`` rather than deleted.
    """
    data_dir, log_dir = Path(data_dir), Path(log_dir)
    moved = []
    names = ("engine.log", "events.jsonl", "context-worker.log",
             "memory-worker.log")
    for name in names:
        source = data_dir / name
        if not source.is_file():
            continue
        target_dir = log_dir / "legacy"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / name
            if target.exists():
                target = target_dir / f"{source.stem}-{int(time.time())}{source.suffix}"
            shutil.move(str(source), str(target))
            moved.append(target)
        except OSError:
            continue
    return moved


class _State:
    def __init__(self, process, log_dir, listener, enqueue, recent, crash_file,
                 previous_hooks):
        self.process, self.log_dir = process, log_dir
        self.listener, self.enqueue, self.recent = listener, enqueue, recent
        self.crash_file = crash_file
        self.previous_hooks = previous_hooks


def _file_handler(path, *, max_bytes, backups, formatter):
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8",
        delay=True)
    handler.setFormatter(formatter)
    return handler


def _shared_errors_handler(path, formatter):
    # Several processes append to errors.jsonl. Size rotation inside a running
    # process would race the others, so it only rotates at startup (see
    # configure_logging); WatchedFileHandler reopens after such a rotation.
    handler = logging.handlers.WatchedFileHandler(path, encoding="utf-8",
                                                  delay=True)
    handler.setFormatter(formatter)
    return handler


def configure_logging(log_dir, *, process, level=logging.INFO, console=False,
                      clock=time.monotonic, rotate_shared=None):
    """Install BoxAgent logging for this process; safe to call again.

    ``rotate_shared`` controls whether this process rotates ``errors.jsonl`` at
    startup; it defaults to True only for the desktop host, which starts
    before every other process.
    """
    global _state
    with _state_lock:
        if _state is not None:
            _shutdown_locked()
        logging.raiseExceptions = False
        log_dir = Path(log_dir)
        formatter = JsonFormatter(process)
        handlers = []
        crash_file = None
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "crash").mkdir(exist_ok=True)
            if rotate_shared if rotate_shared is not None else process == "desktop":
                rotate_if_large(log_dir / "errors.jsonl",
                                max_bytes=ERRORS_MAX_BYTES, backups=ERRORS_BACKUPS)
                prune_old_logs(log_dir)
                rotate_stream_logs(log_dir)
            handlers.append(_file_handler(
                log_dir / f"{process}.jsonl", max_bytes=PROCESS_MAX_BYTES,
                backups=PROCESS_BACKUPS, formatter=formatter))
            handlers.append(RateLimitedHandler(
                _shared_errors_handler(log_dir / "errors.jsonl", formatter),
                clock=clock, accept=_is_problem_or_marker))
            crash_path = log_dir / "crash" / f"{process}.log"
            rotate_if_large(crash_path, max_bytes=1024 * 1024, backups=2)
            crash_file = crash_path.open("a", encoding="utf-8")
            crash_file.write(f"=== {process} pid={os.getpid()} started "
                             f"{iso_time(time.time())}\n")
            crash_file.flush()
            faulthandler.enable(crash_file, all_threads=True)
        except OSError as exc:
            sys.stderr.write(f"[boxagent-logging] 日志目录不可用，改写 stderr：{exc!r}\n")
            fallback = logging.StreamHandler(sys.stderr)
            fallback.setFormatter(formatter)
            handlers = [fallback]
        if console:
            stream = logging.StreamHandler(sys.stderr)
            stream.setFormatter(formatter)
            stream.setLevel(logging.WARNING)
            handlers.append(stream)
        recent = RecentProblems(formatter)
        handlers.append(recent)

        records = queue.SimpleQueue()
        listener = logging.handlers.QueueListener(
            records, *handlers, respect_handler_level=True)
        enqueue = EnqueueHandler(records)
        # One handler on the stdlib root: BoxAgent loggers pass INFO+, every
        # third-party logger (asyncio, websockets…) inherits WARNING+.
        logging.getLogger(ROOT_LOGGER).setLevel(level)
        stdlib_root = logging.getLogger()
        stdlib_root.setLevel(logging.WARNING)
        stdlib_root.addHandler(enqueue)
        listener.start()

        previous = (sys.excepthook, threading.excepthook)
        sys.excepthook = _sys_excepthook
        threading.excepthook = _thread_excepthook
        _state = _State(process, log_dir, listener, enqueue, recent, crash_file,
                        previous)
    logging.getLogger("boxagent.logging").info(
        READY_EVENT, extra={"data": {"log_dir": str(log_dir), "process": process,
                                     "python": sys.version.split()[0]}})
    return _state


def _shutdown_locked():
    global _state
    state, _state = _state, None
    if state is None:
        return
    logging.getLogger().removeHandler(state.enqueue)
    state.listener.stop()
    for handler in state.listener.handlers:
        with contextlib.suppress(Exception):
            handler.close()
    sys.excepthook, threading.excepthook = state.previous_hooks
    if state.crash_file is not None:
        with contextlib.suppress(Exception):
            faulthandler.disable()
            state.crash_file.close()


def shutdown_logging():
    """Flush queued records and detach handlers (tests, orderly exit)."""
    with _state_lock:
        _shutdown_locked()


def flush_logging():
    """Block until queued records are written; keeps logging configured."""
    with _state_lock:
        state = _state
        if state is None:
            return
        state.listener.stop()
        state.listener.start()


def recent_problems():
    """Most recent WARNING+ records of this process, oldest first."""
    state = _state
    return list(state.recent.items) if state else []


def _sys_excepthook(kind, value, tb):
    if not issubclass(kind, KeyboardInterrupt):
        crash_logger.critical("uncaught exception", exc_info=(kind, value, tb))
        flush_logging()
    previous = _state.previous_hooks[0] if _state else sys.__excepthook__
    previous(kind, value, tb)


def _thread_excepthook(args):
    if args.exc_type is not SystemExit:
        crash_logger.critical(
            "uncaught exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    previous = _state.previous_hooks[1] if _state else threading.__excepthook__
    previous(args)
