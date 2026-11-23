"""Supervise the restartable Engine for the Electron local service."""

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
import time
import traceback
from collections.abc import Coroutine, Mapping
from pathlib import Path

from boxagent.core.errors import redact
from boxagent.core.logs import asyncio_exception_handler
from boxagent.core.paths import PACKAGE_ROOT
from boxagent.interfaces.engine import EngineClient, EngineDisconnected, EngineSupervisor

logger = logging.getLogger(__name__)


class EngineBridge:
    WATCH_ROOTS = ("application", "agent", "domain", "infrastructure")
    WATCH_FILES = (
        "bootstrap/engine.py", "bootstrap/settings.py", "entrypoints/engine.py",
        "interfaces/engine/protocol.py", "interfaces/engine/server.py",
    )

    def __init__(self, *, root: Path, data_dir: Path, log_dir: Path,
                 task_provider: str, task_model: str | None = None,
                 auto_approve: bool = True, context_interval: float = 0,
                 context_size: int = 960, watch: bool = True,
                 soul_file: Path | None = None) -> None:
        self.root, self.data_dir, self.log_dir = root, data_dir, log_dir
        self.loop = asyncio.new_event_loop()
        self.socket_path = data_dir / "engine.sock"
        self.supervisor = EngineSupervisor(
            root=root,
            socket_path=self.socket_path,
            log_path=log_dir / "engine.log",
            task_provider=task_provider,
            task_model=task_model,
            auto_approve=auto_approve,
            context_interval=context_interval,
            context_size=context_size,
        )
        self.client = EngineClient(self.socket_path, self.publish)
        self.watch = watch
        self.soul_file = soul_file
        self.closing = False
        self.engine_pid: int | None = None
        self.restart_lock: asyncio.Lock | None = None
        self.background_tasks: list[asyncio.Task[None]] = []
        self.thread = threading.Thread(target=self.run, name="BoxAgent-engine-client", daemon=True)
        self.thread.start()
        self.startup = self.submit(self._start())

    def run(self) -> None:
        asyncio.set_event_loop(self.loop)
        def report(loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
            asyncio_exception_handler(loop, context)
            self.publish({
                "type": "backend.error", "occurred_at": time.time(),
                "error": "后台出现异常，请稍后重试",
                "detail": repr(context.get("exception") or context.get("message")),
            })
        self.loop.set_exception_handler(report)
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    async def _start(self) -> None:
        self.restart_lock = asyncio.Lock()
        adopted = await self._adopt_existing()
        if not adopted:
            await self.supervisor.start()
            await self.client.connect()
        health = await self.client.request("health")
        pid = health.get("pid") if isinstance(health, dict) else None
        self.engine_pid = pid if type(pid) is int else None
        self.publish(
            {
                "type": "backend.connected",
                "occurred_at": time.time(),
                "pid": self.engine_pid,
                "adopted": adopted,
            }
        )
        self.background_tasks.append(
            asyncio.create_task(self._monitor(), name="boxagent-engine-monitor")
        )
        if self.watch:
            self.background_tasks.append(
                asyncio.create_task(self._watch_sources(), name="boxagent-engine-watch")
            )

    async def request(self, method: str, params: Mapping[str, object] | None = None) -> object:
        return await self.client.request(method, params)

    async def _adopt_existing(self) -> bool:
        if not self.socket_path.exists():
            return False
        try:
            await self.client.connect(timeout=.5)
            health = await self.client.request("health", timeout=2)
            return isinstance(health, dict) and health.get("status") == "ready"
        except Exception as exc:
            # A stale socket from a crashed Engine is expected; start a new one.
            logger.info("existing engine not adopted: %s", type(exc).__name__)
            await self.client.disconnect()
            return False

    def publish(self, event: dict[str, object]) -> None:
        if not event:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(event), ensure_ascii=False) + "\n")

    def submit[T](
        self, coroutine: Coroutine[object, object, T]
    ) -> concurrent.futures.Future[T]:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def finished(done: concurrent.futures.Future[T]) -> None:
            try:
                done.result()
            except (concurrent.futures.CancelledError, EngineDisconnected):
                pass
            except Exception as exc:
                logger.error("engine request failed", exc_info=True)
                self.publish({"type": "backend.error", "occurred_at": time.time(),
                              "error": "请求未能处理，请稍后重试", "detail": repr(exc),
                              "traceback": traceback.format_exc()})
        future.add_done_callback(finished)
        return future

    def _source_snapshot(self) -> dict[str, int]:
        snapshot = {}
        for name in self.WATCH_ROOTS:
            base = PACKAGE_ROOT / name
            paths = base.rglob("*.py")
            for path in paths:
                try:
                    snapshot[str(path)] = path.stat().st_mtime_ns
                except OSError:
                    continue
        for name in self.WATCH_FILES:
            path = PACKAGE_ROOT / name
            try:
                snapshot[str(path)] = path.stat().st_mtime_ns
            except OSError:
                continue
        for candidate in {self.data_dir / "SOUL.md", self.soul_file}:
            if candidate is None:
                continue
            try:
                snapshot[str(candidate)] = candidate.stat().st_mtime_ns
            except OSError:
                continue
        return snapshot

    async def _watch_sources(self) -> None:
        previous = await asyncio.to_thread(self._source_snapshot)
        while not self.closing:
            await asyncio.sleep(.75)
            current = await asyncio.to_thread(self._source_snapshot)
            if current != previous:
                previous = current
                await self._restart("source_changed")

    async def _monitor(self) -> None:
        while not self.closing:
            await asyncio.sleep(.5)
            process = self.supervisor.process
            if process and process.returncode is not None:
                await self._restart("process_exited")

    async def _restart(self, reason: str) -> None:
        if self.restart_lock is None:
            raise RuntimeError("Engine has not started")
        async with self.restart_lock:
            if self.closing:
                return
            logger.warning("restarting engine: %s", reason)
            self.publish({"type": "backend.restarting", "occurred_at": time.time(),
                          "reason": reason})
            if self.client.connected.is_set():
                try:
                    await self.client.request("shutdown", timeout=3)
                except Exception:
                    logger.warning("engine did not acknowledge shutdown before restart",
                                   exc_info=True)
            await self.client.disconnect()
            try:
                if self.supervisor.process:
                    await self.supervisor.restart()
                else:
                    deadline = asyncio.get_running_loop().time() + 5
                    while (
                        self.socket_path.exists() and asyncio.get_running_loop().time() < deadline
                    ):
                        await asyncio.sleep(0.05)
                    await self.supervisor.start()
                await self.client.connect()
                health = await self.client.request("health")
                pid = health.get("pid") if isinstance(health, dict) else None
                self.engine_pid = pid if type(pid) is int else None
            except Exception as exc:
                logger.error("engine restart failed", exc_info=True)
                self.publish({"type": "backend.error", "occurred_at": time.time(),
                              "error": "后台重启失败，请重新启动 BoxAgent", "detail": repr(exc)})
                return
            self.publish({"type": "backend.reconnected", "occurred_at": time.time(),
                          "pid": self.engine_pid})

    async def _close(self) -> None:
        self.closing = True
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        if self.client.connected.is_set():
            try:
                await self.client.request("shutdown", timeout=8)
            except Exception:
                logger.warning("engine did not acknowledge shutdown", exc_info=True)
        await self.client.close()
        process = self.supervisor.process
        if process is not None and process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                logger.warning("Engine did not exit after shutdown; terminating")
        await self.supervisor.stop()

    def close(self) -> None:
        if not self.startup.done():
            self.startup.cancel()
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop).result(timeout=12)
        except Exception:
            logger.warning("engine bridge close incomplete", exc_info=True)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.publish({"type": "app.stopped", "occurred_at": time.time(), "pid": os.getpid()})
