"""Local Unix-socket server owning BoxAgentApplication and all Agent services."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from boxagent.core import file_lock as fcntl
from boxagent.interfaces.engine.protocol import PROTOCOL_VERSION, STREAM_LIMIT, decode, encode

if TYPE_CHECKING:
    from boxagent.application.assistant import BoxAgentApplication

logger = logging.getLogger(__name__)

type _Publisher = Callable[[dict[str, object]], None]
type _Outbox = asyncio.Queue[dict[str, object]]


class EngineServer:
    def __init__(
        self,
        socket_path: str | Path,
        application_factory: Callable[[_Publisher], BoxAgentApplication],
        *,
        start_context: bool = False,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.application_factory = application_factory
        self.start_context = start_context
        self.application: BoxAgentApplication | None = None
        self.server: asyncio.Server | None = None
        self.clients: dict[asyncio.StreamWriter, tuple[_Outbox, asyncio.Task[None]]] = {}
        self.stop_requested = asyncio.Event()
        self.lock_stream: TextIO | None = None
        self.auth_token = ""

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_stream = self.socket_path.with_suffix(self.socket_path.suffix + ".lock").open(
            "a+"
        )
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
        if os.name == "nt":
            self.auth_token = secrets.token_hex(32)
            self.server = await asyncio.start_server(
                self._handle_client, host="127.0.0.1", port=0, limit=STREAM_LIMIT)
            sockets = self.server.sockets
            if not sockets:
                raise RuntimeError("Engine 未创建本地监听 socket")
            port = sockets[0].getsockname()[1]
            self.socket_path.write_text(json.dumps({"port": port, "token": self.auth_token}),
                                        encoding="utf-8")
        else:
            self.server = await asyncio.start_unix_server(
                self._handle_client, path=self.socket_path, limit=STREAM_LIMIT)
        os.chmod(self.socket_path, 0o600)

    async def run(self) -> None:
        try:
            await self.start()
            await self.stop_requested.wait()
        finally:
            await self.close()

    def request_stop(self) -> None:
        self.stop_requested.set()

    def publish(self, event: dict[str, object]) -> None:
        if not isinstance(event, dict):
            return
        for writer, (outbox, _task) in tuple(self.clients.items()):
            try:
                outbox.put_nowait({"type": "event", "event": event})
            except asyncio.QueueFull:
                self.clients.pop(writer, None)
                writer.close()

    async def _send(self, writer: asyncio.StreamWriter, message: dict[str, object]) -> None:
        client = self.clients.get(writer)
        if not client:
            raise ConnectionError("Engine client disconnected")
        await client[0].put(message)

    async def _write_client(self, writer: asyncio.StreamWriter, outbox: _Outbox) -> None:
        try:
            while True:
                writer.write(encode(await outbox.get()))
                await writer.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            pass

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self.auth_token:
            try:
                token = (await asyncio.wait_for(reader.readline(), 5)).decode().strip()
                if not secrets.compare_digest(token, self.auth_token):
                    writer.close()
                    await writer.wait_closed()
                    return
            except (TimeoutError, UnicodeError, ValueError, OSError):
                writer.close()
                return
        application = self.application
        if application is None:
            writer.close()
            await writer.wait_closed()
            return
        outbox: _Outbox = asyncio.Queue(maxsize=1000)
        sender = asyncio.create_task(
            self._write_client(writer, outbox), name="boxagent-engine-writer")
        self.clients[writer] = (outbox, sender)
        await self._send(writer, {"type": "event", "event": {
            "type": "engine.ready",
            "pid": os.getpid(),
            "protocol_version": PROTOCOL_VERSION,
            "state": application.state.payload(),
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
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _handle_request(
        self, writer: asyncio.StreamWriter, request: dict[str, object]
    ) -> None:
        request_id = str(request.get("id", ""))
        try:
            if request.get("version") != PROTOCOL_VERSION:
                raise ValueError("Engine 协议版本不兼容")
            if request.get("type") != "request" or not request_id:
                raise ValueError("Engine 请求格式无效")
            method = request.get("method")
            params = request.get("params") or {}
            if not isinstance(method, str) or not isinstance(params, dict):
                raise ValueError("Engine 请求方法或参数无效")
            result = await self.dispatch(method, params)
            response = {"type": "response", "id": request_id, "ok": True,
                        "result": result}
        except Exception as exc:
            logger.error("engine request %s failed", request.get("method"),
                         exc_info=True)
            response = {"type": "response", "id": request_id, "ok": False,
                        "error": str(exc) or type(exc).__name__}
        await self._send(writer, response)
        if request.get("method") == "shutdown" and response.get("ok"):
            self.request_stop()

    async def dispatch(self, method: str, params: dict[str, Any]) -> object:
        if method == "health":
            return {"status": "ready", "pid": os.getpid(),
                    "protocol_version": PROTOCOL_VERSION}
        if method == "shutdown":
            return {"status": "stopping"}
        application = self.application
        if application is None:
            raise RuntimeError("Engine 尚未启动")
        if method == "state":
            return application.state.payload()
        if method == "external_events_status":
            integration = application.external_events
            return await integration.status() if integration else {"enabled": False}
        if method in {"camera_start", "camera_frame", "camera_stop"}:
            camera = application.camera
            if camera is None:
                raise ValueError("Camera feature unavailable in this Engine")
            if method == "camera_start":
                return await camera.start()
            if method == "camera_stop":
                token = params.get("token")
                if not isinstance(token, str):
                    raise ValueError("Camera token required")
                return await camera.stop(token)
            return await camera.send_frame(params)
        if method == "toggle_context":
            await application.toggle_context()
            return {"status": "succeeded"}
        if method == "set_game_companion_mode":
            enabled = params.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("陪玩模式开关必须为布尔值")
            return application.set_game_companion_mode(enabled)
        if method == "toggle_lol_companion":
            return await application.toggle_lol_companion()
        if method == "set_companion_session":
            game, enabled = params.get("game"), params.get("enabled")
            if game not in ("lol", "wuwa") or type(enabled) is not bool:
                raise ValueError("请选择有效的陪玩游戏和开关状态")
            return await application.set_companion_session(game, enabled)
        if method == "set_companion_audio":
            control, enabled = params.get("control"), params.get("enabled")
            if control not in ("microphone", "speech") or type(enabled) is not bool:
                raise ValueError("请选择有效的陪玩音频开关")
            await application.set_companion_audio(control, enabled)
            return {"status": "succeeded"}
        if method == "toggle_voice":
            await application.toggle_voice()
            return {"status": "succeeded"}
        if method == "wake_voice":
            greeting = params.get("greeting")
            if not isinstance(greeting, str) or not greeting.strip() or len(greeting) > 160:
                raise ValueError("语音唤醒问候无效")
            await application.wake_voice(greeting)
            return {"status": "succeeded"}
        if method == "submit_text":
            return await application.submit_text(params.get("goal", ""))
        if method == "list_sessions":
            return await application.list_sessions(
                include_archived=bool(params.get("include_archived")))
        if method == "create_session":
            return await application.create_session(params.get("title", "新会话"))
        if method == "activate_session":
            return await application.activate_session(params.get("session_id", ""))
        if method == "archive_session":
            return await application.archive_session(params.get("session_id", ""))
        if method == "session_events":
            limit = params.get("limit")
            return await application.session_events(
                params.get("session_id", ""),
                after_sequence=int(params.get("after_sequence", 0)),
                limit=int(limit) if limit is not None else None)
        if method == "session_timeline":
            return await application.session_timeline(
                str(params.get("session_id") or ""), after_seq=int(params.get("after_seq", 0)),
                limit=int(params.get("limit", 150)))
        if method == "cancel_task":
            return await application.cancel_task()
        if method == "cancel_all_tasks":
            return await application.cancel_all_tasks()
        if method == "answer_approval":
            await application.answer_approval(bool(params.get("allowed")))
            return {"status": "succeeded"}
        if method == "remember_memory":
            return await application.remember_memory(
                params.get("content", ""), source=params.get("source", "explicit"))
        if method == "recall_memory":
            return await application.recall_memory(
                params.get("query", ""), top_k=int(params.get("top_k", 5)))
        if method == "forget_memory":
            return await application.forget_memory(params.get("memory_ids") or [])
        if method == "context_snapshot":
            return await application.context_snapshot(
                str(params.get("session_id") or ""))
        if method == "context_entry":
            return await application.context_entry(
                str(params.get("session_id") or ""), int(params.get("seq") or 0))
        if method == "memory_snapshot":
            return await application.memory_snapshot(**params)
        if method == "memory_evidence":
            memory_id = params.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id.strip() or len(memory_id) > 200:
                raise ValueError("memory_id 必须是有效的记忆 ID")
            return await application.memory_evidence(memory_id)
        if method == "delete_memory_node":
            return await application.delete_memory_node(params.get("memory_id", ""))
        if method == "list_skills":
            return await application.list_skills()
        if method == "create_skill":
            return await application.create_skill(**params)
        if method == "update_skill":
            return await application.update_skill(**params)
        if method == "set_skill_enabled":
            return await application.set_skill_enabled(
                params.get("skill_id", ""), bool(params.get("enabled")))
        if method == "delete_skill":
            return await application.delete_skill(params.get("skill_id", ""))
        if method == "list_notifications":
            return await application.list_notifications(
                pending_only=bool(params.get("pending_only", True)))
        if method == "acknowledge_notification":
            return await application.acknowledge_notification(
                params.get("notification_id", ""),
                params.get("channel", "ui"),
                params.get("receipt", "presented"))
        raise ValueError(f"未知 Engine 方法：{method}")

    async def close(self) -> None:
        try:
            if self.server:
                self.server.close()
            clients, self.clients = tuple(self.clients.items()), {}
            for writer, (_outbox, sender) in clients:
                sender.cancel()
                writer.close()
            await asyncio.gather(
                *(task for writer, (_outbox, sender) in clients
                  for task in (sender, writer.wait_closed())),
                return_exceptions=True,
            )
            if self.server:
                await self.server.wait_closed()
                self.server = None
            if self.application:
                await self.application.close()
                self.application = None
        finally:
            if self.lock_stream:
                self.socket_path.unlink(missing_ok=True)
                fcntl.flock(self.lock_stream, fcntl.LOCK_UN)
                self.lock_stream.close()
                self.lock_stream = None
