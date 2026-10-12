"""INFRA-03: unified diagnostic logging."""

import ast
import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from boxagent.bootstrap import logs
from boxagent.bootstrap.logs import (
    READY_EVENT,
    RateLimitedHandler,
    configure_logging,
    flush_logging,
    migrate_legacy_logs,
    prune_old_logs,
    recent_problems,
    rotate_if_large,
    shutdown_logging,
)
from boxagent.core.logs import bind_log_context, log_context

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "boxagent"


def rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class LoggingTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temporary.name) / "logs"
        self.saved_hooks = (sys.excepthook, threading.excepthook)

    def tearDown(self):
        shutdown_logging()
        self.assertEqual((sys.excepthook, threading.excepthook), self.saved_hooks)
        self.temporary.cleanup()


class JsonRecordTests(LoggingTestCase):
    def test_process_file_gets_info_and_errors_file_gets_marker_and_warnings(self):
        configure_logging(self.log_dir, process="engine")
        logger = logging.getLogger("boxagent.tests.sample")
        logger.debug("too detailed")
        logger.info("turn finished", extra={"data": {"ms": 12}})
        logger.warning("recall degraded")
        flush_logging()

        own = rows(self.log_dir / "engine.jsonl")
        self.assertEqual([row["msg"] for row in own],
                         [READY_EVENT, "turn finished", "recall degraded"])
        self.assertEqual(own[1]["data"], {"ms": 12})
        self.assertEqual(own[1]["process"], "engine")
        self.assertEqual(own[1]["pid"], os.getpid())
        self.assertEqual(own[1]["logger"], "boxagent.tests.sample")
        self.assertTrue(own[1]["ts"].endswith("+00:00"))
        shared = rows(self.log_dir / "errors.jsonl")
        self.assertEqual([row["msg"] for row in shared], [READY_EVENT, "recall degraded"])

    def test_exception_stack_and_type_are_kept(self):
        configure_logging(self.log_dir, process="engine")
        try:
            raise TimeoutError("memory worker slow")
        except TimeoutError:
            logging.getLogger("boxagent.x").warning("recall failed", exc_info=True)
        flush_logging()
        row = rows(self.log_dir / "errors.jsonl")[-1]
        self.assertEqual(row["exc_type"], "TimeoutError")
        self.assertIn("memory worker slow", row["exc"])
        self.assertIn("Traceback", row["exc"])

    def test_secrets_are_redacted(self):
        configure_logging(self.log_dir, process="engine")
        logging.getLogger("boxagent.x").warning(
            "auth failed with sk-abcdefghijklmnopqrstu",
            extra={"data": {"api_key": "plain", "header": "Bearer xyz"}})
        flush_logging()
        text = (self.log_dir / "errors.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefghijklmnopqrstu", text)
        self.assertNotIn("plain", text)
        self.assertNotIn("Bearer xyz", text)

    def test_third_party_warnings_are_captured_but_not_their_info(self):
        configure_logging(self.log_dir, process="engine")
        logging.getLogger("websockets.client").info("frame")
        logging.getLogger("websockets.client").warning("closed abnormally")
        flush_logging()
        messages = [row["msg"] for row in rows(self.log_dir / "engine.jsonl")]
        self.assertIn("closed abnormally", messages)
        self.assertNotIn("frame", messages)

    def test_recent_problems_ring_for_admin_view(self):
        configure_logging(self.log_dir, process="engine")
        logging.getLogger("boxagent.x").info("fine")
        logging.getLogger("boxagent.x").error("broken")
        flush_logging()
        self.assertEqual([row["msg"] for row in recent_problems()], ["broken"])


class CorrelationTests(LoggingTestCase):
    def test_context_ids_are_attached_and_restored(self):
        configure_logging(self.log_dir, process="engine")
        logger = logging.getLogger("boxagent.x")
        with log_context(session_id="ses_1", interaction_id="int_1"):
            logger.info("inside")
        logger.info("outside")
        flush_logging()
        inside, outside = rows(self.log_dir / "engine.jsonl")[-2:]
        self.assertEqual((inside["session_id"], inside["interaction_id"]), ("ses_1", "int_1"))
        self.assertNotIn("session_id", outside)

    def test_ids_follow_each_asyncio_task_independently(self):
        configure_logging(self.log_dir, process="engine")
        logger = logging.getLogger("boxagent.x")

        async def turn(session):
            bind_log_context(session_id=session)
            await asyncio.sleep(0)
            logger.info("turn %s", session)

        async def main():
            await asyncio.gather(turn("ses_a"), turn("ses_b"))

        asyncio.run(main())
        flush_logging()
        found = {row["msg"]: row.get("session_id") for row in rows(self.log_dir / "engine.jsonl")}
        self.assertEqual(found["turn ses_a"], "ses_a")
        self.assertEqual(found["turn ses_b"], "ses_b")

    def test_unknown_context_field_is_rejected(self):
        with self.assertRaises(ValueError):
            bind_log_context(sesion_id="typo")

    def test_interaction_start_binds_session_and_interaction(self):
        from boxagent.domain.conversation.service import ConversationService
        from boxagent.infrastructure.persistence.jsonl_session_repository import JsonlSessionStore

        configure_logging(self.log_dir, process="engine")

        async def main():
            service = ConversationService(JsonlSessionStore(Path(self.temporary.name) / "c"))
            interaction = await service.begin_interaction("你好", source="text",
                                                          runtime="qwen_realtime")
            logging.getLogger("boxagent.x").warning("after start")
            return interaction

        interaction = asyncio.run(main())
        flush_logging()
        row = rows(self.log_dir / "errors.jsonl")[-1]
        self.assertEqual(row["session_id"], interaction.session.session_id)
        self.assertEqual(row["interaction_id"], interaction.interaction_id)


class RateLimitTests(unittest.TestCase):
    def test_excess_is_dropped_and_reported_in_next_window(self):
        received = []

        class Sink(logging.Handler):
            def emit(self, record):
                received.append(record.getMessage())

        now = [0.0]
        handler = RateLimitedHandler(Sink(), limit=3, window=60, clock=lambda: now[0])
        logger = logging.getLogger("boxagent.tests.rate")
        logger.propagate = False
        logger.addHandler(handler)
        try:
            for index in range(10):
                logger.warning("loop %d", index)
            now[0] = 61
            logger.warning("next window")
        finally:
            logger.removeHandler(handler)
            logger.propagate = True
        self.assertEqual(received, ["loop 0", "loop 1", "loop 2",
                                    "dropped 7 message(s) over the rate limit",
                                    "next window"])


class FailureIsolationTests(LoggingTestCase):
    def test_unwritable_log_dir_falls_back_to_stderr_and_never_raises(self):
        blocker = Path(self.temporary.name) / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        configure_logging(blocker / "logs", process="engine")
        logging.getLogger("boxagent.x").error("still fine")
        flush_logging()

    def test_handler_failure_does_not_reach_caller(self):
        configure_logging(self.log_dir, process="engine")
        (self.log_dir / "engine.jsonl").unlink(missing_ok=True)
        # A record that cannot be formatted must not raise into the caller.
        logging.getLogger("boxagent.x").warning("bad %d", "not-a-number")
        logging.getLogger("boxagent.x").warning("after bad record")
        flush_logging()
        self.assertIn("after bad record",
                      [row["msg"] for row in rows(self.log_dir / "errors.jsonl")])

    def test_configure_twice_replaces_previous_handlers(self):
        configure_logging(self.log_dir, process="engine")
        configure_logging(self.log_dir, process="engine")
        logging.getLogger("boxagent.x").warning("once")
        flush_logging()
        messages = [row["msg"] for row in rows(self.log_dir / "errors.jsonl")]
        self.assertEqual(messages.count("once"), 1)


class UncaughtExceptionTests(LoggingTestCase):
    def test_thread_exception_is_logged(self):
        configure_logging(self.log_dir, process="engine")
        previous = logs._state.previous_hooks
        logs._state.previous_hooks = (previous[0], lambda args: None)

        def boom():
            raise RuntimeError("worker thread died")

        thread = threading.Thread(target=boom, name="probe")
        thread.start()
        thread.join()
        logs._state.previous_hooks = previous
        flush_logging()
        row = rows(self.log_dir / "errors.jsonl")[-1]
        self.assertEqual(row["level"], "critical")
        self.assertIn("probe", row["msg"])
        self.assertEqual(row["exc_type"], "RuntimeError")

    def test_asyncio_task_exception_is_logged(self):
        configure_logging(self.log_dir, process="engine")

        async def main():
            loop = logs.install_asyncio_handler()
            loop.call_exception_handler({"message": "Task exception was never retrieved",
                                         "exception": ValueError("lost")})

        asyncio.run(main())
        flush_logging()
        row = rows(self.log_dir / "errors.jsonl")[-1]
        self.assertEqual(row["logger"], "boxagent.crash")
        self.assertEqual(row["exc_type"], "ValueError")

    def test_process_crash_reaches_errors_file(self):
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from boxagent.bootstrap.logs import configure_logging\n"
            "configure_logging(%r, process='probe')\n"
            "raise SystemError('fatal startup')\n") % (str(ROOT), str(self.log_dir))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                                text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        row = rows(self.log_dir / "errors.jsonl")[-1]
        self.assertEqual(row["msg"], "uncaught exception")
        self.assertEqual(row["process"], "probe")
        self.assertTrue((self.log_dir / "crash/probe.log").is_file())


class FileMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_rotate_keeps_bounded_backups(self):
        path = self.root / "events.jsonl"
        for round_ in range(4):
            path.write_text("x" * 20 + str(round_), encoding="utf-8")
            rotate_if_large(path, max_bytes=10, backups=2)
        self.assertFalse(path.exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["events.jsonl.1", "events.jsonl.2"])
        self.assertTrue((self.root / "events.jsonl.1").read_text().endswith("3"))

    def test_prune_removes_only_old_backups(self):
        old = self.root / "engine.jsonl.3"
        fresh = self.root / "engine.jsonl.1"
        live = self.root / "engine.jsonl"
        for path in (old, fresh, live):
            path.write_text("{}", encoding="utf-8")
        long_ago = time.time() - 30 * 86400
        os.utime(old, (long_ago, long_ago))
        os.utime(live, (long_ago, long_ago))
        removed = prune_old_logs(self.root)
        self.assertEqual(removed, [old])
        self.assertTrue(fresh.exists() and live.exists())

    def test_legacy_root_logs_move_under_logs_once(self):
        data, log_dir = self.root / "pet", self.root / "pet/logs"
        data.mkdir()
        (data / "engine.log").write_text("old", encoding="utf-8")
        (data / "events.jsonl").write_text("{}", encoding="utf-8")
        moved = migrate_legacy_logs(data, log_dir)
        self.assertEqual(sorted(p.name for p in moved), ["engine.log", "events.jsonl"])
        self.assertFalse((data / "engine.log").exists())
        self.assertEqual((log_dir / "legacy/engine.log").read_text(), "old")
        self.assertEqual(migrate_legacy_logs(data, log_dir), [])


class DesktopShutdownTests(unittest.TestCase):
    # AppKit 不在测试环境中，按源码检查退出顺序：先关 Engine，最后写完日志。
    def test_terminate_flushes_logs_after_backend_close(self):
        tree = ast.parse((PACKAGE / "interfaces/macos/app.py").read_text(encoding="utf-8"))
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "applicationShouldTerminate_")
        calls = [ast.unparse(node.func) for node in ast.walk(method)
                 if isinstance(node, ast.Call)]
        self.assertIn("self.on_terminated", calls)
        self.assertLess(calls.index("self.backend.close"), calls.index("self.on_terminated"))

    def test_bootstrap_wires_log_shutdown_into_desktop(self):
        source = (PACKAGE / "bootstrap/desktop.py").read_text(encoding="utf-8")
        self.assertIn("on_terminated=shutdown_logging", source)


class NoSilentSwallowTests(unittest.TestCase):
    """A broad ``except`` must log, re-raise or report; never vanish."""

    # Vendored research code and shutdown paths where logging is unavailable.
    EXEMPT_PREFIXES = ("infrastructure/memory/jev_mem/",)
    EXEMPT = {
        # The logging modules themselves must not log about their own failures.
        "core/logs.py", "bootstrap/logs.py",
        # Native SDK teardown: each call may legitimately be absent.
        "infrastructure/runtimes/qwen/aoq.py",
    }

    def test_no_new_silent_broad_except(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            relative = path.relative_to(PACKAGE).as_posix()
            if relative in self.EXEMPT or relative.startswith(self.EXEMPT_PREFIXES):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                caught = ast.unparse(node.type) if node.type else "bare"
                if caught not in {"bare", "Exception", "BaseException"}:
                    continue
                if self._handled(node):
                    continue
                offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [], "broad except without log/raise/report")

    @staticmethod
    def _handled(node):
        for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            if isinstance(child, ast.Raise):
                return True
            if isinstance(child, ast.Call):
                name = ast.unparse(child.func)
                # Logged, published to the UI, returned to the RPC caller, or
                # stored as a structured failure the caller inspects.
                if name.split(".")[-1] in {
                        "debug", "info", "warning", "error", "exception", "critical",
                        "publish", "emit", "_report", "log", "set_exception",
                        "print_exc", "write", "_finish_failed"}:
                    return True
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load) \
                    and child.id == (node.name or ""):
                # The exception value is used: turned into a result or message.
                return True
        return False


if __name__ == "__main__":
    unittest.main()
