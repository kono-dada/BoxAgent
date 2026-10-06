"""Async client for the local BoxAgent Engine."""

import asyncio
import itertools

from boxagent.interfaces.engine.protocol import PROTOCOL_VERSION, decode, encode


class EngineDisconnected(ConnectionError):
    pass


class EngineClient:
    def __init__(self, socket_path, publish):
        self.socket_path = str(socket_path)
        self.publish = publish
        self.reader = self.writer = self.reader_task = None
        self.pending = {}
        self.sequence = itertools.count(1)
        self.write_lock = asyncio.Lock()
        self.connected = asyncio.Event()
        self.closed = False

    async def connect(self, timeout=20):
        deadline = asyncio.get_running_loop().time() + timeout
        last_error = None
        while not self.closed and asyncio.get_running_loop().time() < deadline:
            try:
                self.reader, self.writer = await asyncio.open_unix_connection(self.socket_path)
                self.reader_task = asyncio.create_task(self._read(), name="boxagent-engine-reader")
                self.connected.set()
                return
            except (ConnectionError, FileNotFoundError, OSError) as exc:
                last_error = exc
                await asyncio.sleep(.1)
        raise EngineDisconnected(f"无法连接 BoxAgent Engine：{last_error}")

    async def request(self, method, params=None, timeout=60):
        if self.closed:
            raise EngineDisconnected("BoxAgent Engine 客户端已关闭")
        try:
            await asyncio.wait_for(self.connected.wait(), timeout=min(timeout, 20))
        except TimeoutError as exc:
            raise EngineDisconnected("BoxAgent Engine 尚未就绪") from exc
        request_id = str(next(self.sequence))
        future = asyncio.get_running_loop().create_future()
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
                    "params": params or {},
                }))
                await self.writer.drain()
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _read(self):
        reason = "BoxAgent Engine 连接已关闭"
        try:
            while line := await self.reader.readline():
                message = decode(line)
                if message.get("type") == "event":
                    self.publish(message.get("event") or {})
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

    async def disconnect(self):
        self.connected.clear()
        task, writer = self.reader_task, self.writer
        self.reader_task = self.reader = self.writer = None
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        self.closed = True
        await self.disconnect()
