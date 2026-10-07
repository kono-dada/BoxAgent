"""Local Unix-socket server owning BoxAgentApplication and all Agent services."""

import asyncio
import fcntl
import os
from pathlib import Path

from boxagent.interfaces.engine.protocol import PROTOCOL_VERSION, decode, encode


class EngineServer:
    def __init__(self, socket_path, application_factory, *, start_context=False):
        self.socket_path = Path(socket_path)
        self.application_factory = application_factory
        self.start_context = start_context
        self.application = None
        self.server = None
        self.clients = {}
        self.stop_requested = asyncio.Event()
        self.lock_stream = None

    async def start(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_stream = self.socket_path.with_suffix(self.socket_path.suffix + ".lock").open("w")
        try:
            fcntl.flock(self.lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock_stream.close()
            self.lock_stream = None
            raise RuntimeError("另一个 BoxAgent Engine 已持有此数据目录") from exc
        self.socket_path.unlink(missing_ok=True)
        self.application = self.application_factory(self.publish)
        await self.application.start()
        if self.start_context:
            await self.application.toggle_context()
        self.server = await asyncio.start_unix_server(self._handle_client, path=self.socket_path)
        os.chmod(self.socket_path, 0o600)

    async def run(self):
        try:
            await self.start()
            await self.stop_requested.wait()
        finally:
            await self.close()

    def request_stop(self):
        self.stop_requested.set()

    def publish(self, event):
        if not isinstance(event, dict):
            return
        for writer, (outbox, _task) in tuple(self.clients.items()):
            try:
                outbox.put_nowait({"type": "event", "event": event})
            except asyncio.QueueFull:
                self.clients.pop(writer, None)
                writer.close()

    async def _send(self, writer, message):
        client = self.clients.get(writer)
        if not client:
            raise ConnectionError("Engine client disconnected")
        await client[0].put(message)

    async def _write_client(self, writer, outbox):
        try:
            while True:
                writer.write(encode(await outbox.get()))
                await writer.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass

    async def _handle_client(self, reader, writer):
        outbox = asyncio.Queue(maxsize=1000)
        sender = asyncio.create_task(
            self._write_client(writer, outbox), name="boxagent-engine-writer")
        self.clients[writer] = (outbox, sender)
        await self._send(writer, {"type": "event", "event": {
            "type": "engine.ready",
            "pid": os.getpid(),
            "protocol_version": PROTOCOL_VERSION,
            "state": self.application.state.payload(),
        }})
        try:
            while line := await reader.readline():
                request = decode(line)
                await self._handle_request(writer, request)
        except (ConnectionError, ValueError, OSError):
            pass
        finally:
            _outbox, sender = self.clients.pop(writer, (None, sender))
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _handle_request(self, writer, request):
        request_id = str(request.get("id", ""))
        try:
            if request.get("version") != PROTOCOL_VERSION:
                raise ValueError("Engine 协议版本不兼容")
            if request.get("type") != "request" or not request_id:
                raise ValueError("Engine 请求格式无效")
            method = request.get("method")
            result = await self.dispatch(method, request.get("params") or {})
            response = {"type": "response", "id": request_id, "ok": True,
                        "result": result}
        except Exception as exc:
            response = {"type": "response", "id": request_id, "ok": False,
                        "error": str(exc) or type(exc).__name__}
        await self._send(writer, response)
        if request.get("method") == "shutdown" and response.get("ok"):
            self.request_stop()

    async def dispatch(self, method, params):
        if method == "health":
            return {"status": "ready", "pid": os.getpid(),
                    "protocol_version": PROTOCOL_VERSION}
        if method == "state":
            return self.application.state.payload()
        if method == "toggle_context":
            await self.application.toggle_context()
            return {"status": "succeeded"}
        if method == "toggle_voice":
            await self.application.toggle_voice()
            return {"status": "succeeded"}
        if method == "submit_text":
            return await self.application.submit_text(params.get("goal", ""))
        if method == "list_sessions":
            return await self.application.list_sessions(
                include_archived=bool(params.get("include_archived")))
        if method == "create_session":
            return await self.application.create_session(params.get("title", "新会话"))
        if method == "activate_session":
            return await self.application.activate_session(params.get("session_id", ""))
        if method == "archive_session":
            return await self.application.archive_session(params.get("session_id", ""))
        if method == "session_events":
            limit = params.get("limit")
            return await self.application.session_events(
                params.get("session_id", ""),
                after_sequence=int(params.get("after_sequence", 0)),
                limit=int(limit) if limit is not None else None)
        if method == "cancel_task":
            await self.application.cancel_task()
            return self.application.last_result
        if method == "answer_approval":
            await self.application.answer_approval(bool(params.get("allowed")))
            return {"status": "succeeded"}
        if method == "remember_memory":
            return await self.application.remember_memory(
                params.get("content", ""), source=params.get("source", "explicit"))
        if method == "recall_memory":
            return await self.application.recall_memory(
                params.get("query", ""), top_k=int(params.get("top_k", 5)))
        if method == "forget_memory":
            return await self.application.forget_memory(params.get("memory_ids") or [])
        if method == "memory_snapshot":
            return await self.application.memory_snapshot(**params)
        if method == "delete_memory_node":
            return await self.application.delete_memory_node(params.get("memory_id", ""))
        if method == "list_skills":
            return await self.application.list_skills()
        if method == "create_skill":
            return await self.application.create_skill(**params)
        if method == "update_skill":
            return await self.application.update_skill(**params)
        if method == "set_skill_enabled":
            return await self.application.set_skill_enabled(
                params.get("skill_id", ""), bool(params.get("enabled")))
        if method == "delete_skill":
            return await self.application.delete_skill(params.get("skill_id", ""))
        if method == "list_notifications":
            return await self.application.list_notifications(
                pending_only=bool(params.get("pending_only", True)))
        if method == "acknowledge_notification":
            return await self.application.acknowledge_notification(
                params.get("notification_id", ""),
                params.get("channel", "ui"),
                params.get("receipt", "presented"))
        if method == "shutdown":
            return {"status": "stopping"}
        raise ValueError(f"未知 Engine 方法：{method}")

    async def close(self):
        try:
            if self.server:
                self.server.close()
                await self.server.wait_closed()
                self.server = None
            if self.application:
                await self.application.close()
                self.application = None
            clients, self.clients = tuple(self.clients.items()), {}
            for writer, (_outbox, sender) in clients:
                sender.cancel()
                writer.close()
            await asyncio.gather(
                *(task for writer, (_outbox, sender) in clients
                  for task in (sender, writer.wait_closed())),
                return_exceptions=True,
            )
        finally:
            self.socket_path.unlink(missing_ok=True)
            if self.lock_stream:
                fcntl.flock(self.lock_stream, fcntl.LOCK_UN)
                self.lock_stream.close()
                self.lock_stream = None
