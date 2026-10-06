"""Keep AppKit alive while a restartable Engine process owns backend services."""

import asyncio
import concurrent.futures
import json
import os
import queue
import threading
import time
import traceback

from boxagent.core.errors import redact
from boxagent.core.states import Snapshot
from boxagent.interfaces.engine import EngineClient, EngineDisconnected, EngineSupervisor


class EngineApplicationProxy:
    def __init__(self, bridge):
        self.bridge = bridge
        self.state = Snapshot()

    async def toggle_context(self):
        return await self.bridge.request("toggle_context")

    async def toggle_voice(self):
        return await self.bridge.request("toggle_voice")

    async def submit_text(self, goal):
        return await self.bridge.request("submit_text", {"goal": goal})

    async def list_sessions(self, *, include_archived=False):
        return await self.bridge.request(
            "list_sessions", {"include_archived": include_archived})

    async def create_session(self, title="新会话"):
        return await self.bridge.request("create_session", {"title": title})

    async def activate_session(self, session_id):
        return await self.bridge.request(
            "activate_session", {"session_id": session_id})

    async def archive_session(self, session_id):
        return await self.bridge.request(
            "archive_session", {"session_id": session_id})

    async def session_events(self, session_id, *, after_sequence=0, limit=None):
        params = {"session_id": session_id, "after_sequence": after_sequence}
        if limit is not None:
            params["limit"] = limit
        return await self.bridge.request("session_events", params)

    async def cancel_task(self):
        return await self.bridge.request("cancel_task")

    async def answer_approval(self, allowed):
        return await self.bridge.request("answer_approval", {"allowed": allowed})

    async def remember_memory(self, content, *, source="explicit"):
        return await self.bridge.request("remember_memory", {"content": content, "source": source})

    async def recall_memory(self, query, *, top_k=5):
        return await self.bridge.request("recall_memory", {"query": query, "top_k": top_k})

    async def forget_memory(self, memory_ids):
        return await self.bridge.request("forget_memory", {"memory_ids": memory_ids})

    async def memory_snapshot(self, **arguments):
        return await self.bridge.request("memory_snapshot", arguments)

    async def pending_memories(self):
        return await self.bridge.request("pending_memories")

    async def approve_memory(self, memory_id):
        return await self.bridge.request("approve_memory", {"memory_id": memory_id})

    async def reject_memory(self, memory_id):
        return await self.bridge.request("reject_memory", {"memory_id": memory_id})

    async def retry_memory_index(self, memory_id):
        return await self.bridge.request(
            "retry_memory_index", {"memory_id": memory_id})

    async def delete_memory_node(self, memory_id):
        return await self.bridge.request("delete_memory_node", {"memory_id": memory_id})

    async def list_skills(self):
        return await self.bridge.request("list_skills")

    async def create_skill(self, **params):
        return await self.bridge.request("create_skill", params)

    async def update_skill(self, **params):
        return await self.bridge.request("update_skill", params)

    async def set_skill_enabled(self, skill_id, enabled):
        return await self.bridge.request(
            "set_skill_enabled", {"skill_id": skill_id, "enabled": enabled})

    async def delete_skill(self, skill_id):
        return await self.bridge.request("delete_skill", {"skill_id": skill_id})

    async def list_notifications(self, *, pending_only=True):
        return await self.bridge.request(
            "list_notifications", {"pending_only": pending_only})

    async def acknowledge_notification(self, notification_id, channel="ui",
                                       receipt="presented"):
        return await self.bridge.request("acknowledge_notification", {
            "notification_id": notification_id,
            "channel": channel,
            "receipt": receipt,
        })


class EngineBridge:
    WATCH_ROOTS = ("application", "agent", "domain", "infrastructure")
    WATCH_FILES = (
        "bootstrap/engine.py", "bootstrap/settings.py", "entrypoints/engine.py",
        "interfaces/engine/protocol.py", "interfaces/engine/server.py",
    )

    def __init__(self, *, root, data_dir, log_dir, task_provider, task_model=None,
                 auto_approve=True, context_interval=15, context_size=960, watch=True):
        self.root, self.data_dir, self.log_dir = root, data_dir, log_dir
        self.events = queue.SimpleQueue()
        self.loop = asyncio.new_event_loop()
        self.application = EngineApplicationProxy(self)
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
        self.closing = False
        self.engine_pid = None
        self.restart_lock = None
        self.background_tasks = []
        self.thread = threading.Thread(target=self.run, name="BoxAgent-engine-client", daemon=True)
        self.thread.start()
        self.startup = self.submit(self._start())

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.set_exception_handler(lambda _loop, context: self.publish({
            "type": "backend.error", "occurred_at": time.time(),
            "error": "后台出现异常，请稍后重试",
            "detail": repr(context.get("exception") or context.get("message")),
        }))
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()

    async def _start(self):
        self.restart_lock = asyncio.Lock()
        adopted = await self._adopt_existing()
        if not adopted:
            await self.supervisor.start()
            await self.client.connect()
        health = await self.client.request("health")
        self.engine_pid = health.get("pid")
        await self._recover_notifications()
        self.publish({"type": "backend.connected", "occurred_at": time.time(),
                      "pid": self.engine_pid, "adopted": adopted})
        self.background_tasks.append(asyncio.create_task(self._monitor(), name="boxagent-engine-monitor"))
        if self.watch:
            self.background_tasks.append(asyncio.create_task(self._watch_sources(), name="boxagent-engine-watch"))

    async def request(self, method, params=None):
        return await self.client.request(method, params)

    async def _adopt_existing(self):
        if not self.socket_path.exists():
            return False
        try:
            await self.client.connect(timeout=.5)
            health = await self.client.request("health", timeout=2)
            return health.get("status") == "ready"
        except Exception:
            await self.client.disconnect()
            return False

    def publish(self, event):
        if not event:
            return
        if state := event.get("state"):
            try:
                self.application.state = Snapshot(**state)
            except TypeError:
                pass
        self.events.put(event)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(event), ensure_ascii=False) + "\n")

    def submit(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def finished(done):
            try:
                done.result()
            except (concurrent.futures.CancelledError, EngineDisconnected):
                pass
            except Exception as exc:
                self.publish({"type": "backend.error", "occurred_at": time.time(),
                              "error": "请求未能处理，请稍后重试", "detail": repr(exc),
                              "traceback": traceback.format_exc()})
        future.add_done_callback(finished)
        return future

    def _source_snapshot(self):
        snapshot = {}
        for name in self.WATCH_ROOTS:
            base = self.root / "boxagent" / name
            paths = base.rglob("*.py")
            for path in paths:
                try:
                    snapshot[str(path)] = path.stat().st_mtime_ns
                except OSError:
                    continue
        for name in self.WATCH_FILES:
            path = self.root / "boxagent" / name
            try:
                snapshot[str(path)] = path.stat().st_mtime_ns
            except OSError:
                continue
        return snapshot

    async def _watch_sources(self):
        previous = await asyncio.to_thread(self._source_snapshot)
        while not self.closing:
            await asyncio.sleep(.75)
            current = await asyncio.to_thread(self._source_snapshot)
            if current != previous:
                previous = current
                await self._restart("source_changed")

    async def _monitor(self):
        while not self.closing:
            await asyncio.sleep(.5)
            process = self.supervisor.process
            if process and process.returncode is not None:
                await self._restart("process_exited")

    async def _restart(self, reason):
        async with self.restart_lock:
            if self.closing:
                return
            self.publish({"type": "backend.restarting", "occurred_at": time.time(),
                          "reason": reason})
            if self.client.connected.is_set():
                try:
                    await self.client.request("shutdown", timeout=3)
                except Exception:
                    pass
            await self.client.disconnect()
            try:
                if self.supervisor.process:
                    await self.supervisor.restart()
                else:
                    deadline = asyncio.get_running_loop().time() + 5
                    while self.socket_path.exists() and asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(.05)
                    await self.supervisor.start()
                await self.client.connect()
                health = await self.client.request("health")
                self.engine_pid = health.get("pid")
                await self._recover_notifications()
            except Exception as exc:
                self.publish({"type": "backend.error", "occurred_at": time.time(),
                              "error": "后台重启失败，请重新启动 BoxAgent", "detail": repr(exc)})
                return
            self.publish({"type": "backend.reconnected", "occurred_at": time.time(),
                          "pid": self.engine_pid})

    async def _recover_notifications(self):
        for item in await self.client.request(
                "list_notifications", {"pending_only": True}):
            self.publish({
                "type": "notification.system_requested",
                "occurred_at": time.time(),
                "notification": item,
                "state": {**self.application.state.payload(),
                          "notification_id": item["notification_id"],
                          "notification_text": item["summary"],
                          "notification_unread": True,
                          "revision": self.application.state.revision + 1},
            })

    async def _close(self):
        self.closing = True
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        if self.client.connected.is_set():
            try:
                await self.client.request("shutdown", timeout=8)
            except Exception:
                pass
        await self.client.close()
        await self.supervisor.stop()

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop).result(timeout=12)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
        self.publish({"type": "app.stopped", "occurred_at": time.time(), "pid": os.getpid()})
