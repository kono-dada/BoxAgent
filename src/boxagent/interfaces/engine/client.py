"""Async client for the local BoxAgent Engine."""

import asyncio
import contextlib
import itertools
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from boxagent.interfaces.engine.protocol import PROTOCOL_VERSION, STREAM_LIMIT, decode, encode


class EngineDisconnected(ConnectionError):
    pass


class EngineClient:
    def __init__(
        self, socket_path: str | Path, publish: Callable[[dict[str, object]], None]
    ) -> None:
        self.socket_path = str(socket_path)
        self.publish = publish
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.pending: dict[str, asyncio.Future[object]] = {}
        self.sequence = itertools.count(1)
        self.write_lock = asyncio.Lock()
        self.connected = asyncio.Event()
        self.closed = False

    async def connect(self, timeout: float = 20) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        last_error: Exception | None = None
        while not self.closed and asyncio.get_running_loop().time() < deadline:
            try:
                if os.name == "nt":
                    endpoint: object = json.loads(
                        Path(self.socket_path).read_text(encoding="utf-8")
                    )
                    if not isinstance(endpoint, dict):
                        raise ConnectionError("Invalid Engine endpoint")
                    port, token = endpoint.get("port"), endpoint.get("token")
                    if type(port) is not int or not 0 < port < 65536 or not isinstance(token, str):
                        raise ConnectionError("Invalid Engine endpoint")
                    self.reader, self.writer = await asyncio.open_connection(
                        "127.0.0.1", port, limit=STREAM_LIMIT)
                    self.writer.write((token + "\n").encode())
                    await self.writer.drain()
                else:
                    self.reader, self.writer = await asyncio.open_unix_connection(
                        self.socket_path, limit=STREAM_LIMIT)
                self.reader_task = asyncio.create_task(self._read(), name="boxagent-engine-reader")
                self.connected.set()
                return
            except (ConnectionError, OSError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(.1)
        raise EngineDisconnected(f"无法连接 BoxAgent Engine：{last_error}")

    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        timeout: float = 60,
    ) -> object:
        if self.closed:
            raise EngineDisconnected("BoxAgent Engine 客户端已关闭")
        try:
            await asyncio.wait_for(self.connected.wait(), timeout=min(timeout, 20))
        except TimeoutError as exc:
            raise EngineDisconnected("BoxAgent Engine 尚未就绪") from exc
        request_id = str(next(self.sequence))
        future: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            async with self.write_lock:
                if not self.writer or self.writer.is_closing():
                    raise EngineDisconnected("BoxAgent Engine 连接已断开")
                self.writer.write(encode({
                    "version": PROTOCOL_VERSION,
                    "type": "request",
                    "id": request_id,
                    "method": method,
                    "params": dict(params or {}),
                }))
                await self.writer.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _read(self) -> None:
        reason = "BoxAgent Engine 连接已关闭"
        try:
            reader = self.reader
            if reader is None:
                raise EngineDisconnected("BoxAgent Engine 连接已断开")
            while line := await reader.readline():
                message = decode(line)
                if message.get("type") == "event":
                    event = message.get("event")
                    if isinstance(event, dict):
                        self.publish(event)
                    continue
                if message.get("type") != "response":
                    continue
                future = self.pending.get(str(message.get("id")))
                if not future or future.done():
                    continue
                if message.get("ok"):
                    future.set_result(message.get("result"))
                else:
                    future.set_exception(RuntimeError(message.get("error") or "Engine 请求失败"))
        except asyncio.CancelledError:
            reason = "BoxAgent Engine 连接已取消"
            raise
        except Exception as exc:
            reason = f"BoxAgent Engine 通信中断：{exc}"
        finally:
            self.connected.clear()
            for future in tuple(self.pending.values()):
                if not future.done():
                    future.set_exception(EngineDisconnected(reason))

    async def disconnect(self) -> None:
        self.connected.clear()
        task, writer = self.reader_task, self.writer
        self.reader_task = None
        self.reader = None
        self.writer = None
        if writer:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        self.closed = True
        await self.disconnect()
