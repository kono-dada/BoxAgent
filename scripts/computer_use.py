# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""通过本机 MCP 执行器读取和操作 App，不调用任何模型。"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Awaitable, Callable


ApprovalHandler = Callable[[dict], Awaitable[bool]]


class ComputerUseAuthenticationError(RuntimeError):
    """执行器拒绝调用进程的认证；与用户是否允许目标 App 是两层检查。"""


class ComputerUse:
    """异步 MCP 客户端；默认拒绝授权请求，由宿主提供授权回调。"""

    def __init__(self, approve: ApprovalHandler | None = None, timeout: float = 20):
        self.approve = approve
        self.timeout = timeout
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stderr_reader: asyncio.Task | None = None
        self.callbacks: set[asyncio.Task] = set()
        self.pending: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.write_lock = asyncio.Lock()

    async def __aenter__(self) -> ComputerUse:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        executable = codex_home / (
            "computer-use/Codex Computer Use.app/Contents/SharedSupport/"
            "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
        )
        if not executable.is_file():
            raise FileNotFoundError(f"找不到本机 Computer Use 执行器：{executable}")
        # 不继承当前 Codex 会话的令牌或运行时环境变量。
        env = {key: os.environ[key] for key in
               ("HOME", "PATH", "TMPDIR", "LANG", "CODEX_HOME") if key in os.environ}
        self.process = await asyncio.create_subprocess_exec(
            str(executable), "mcp", env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # 截图以单行 base64 返回，不能使用 asyncio 默认的 64 KiB 行限制。
            limit=64 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self._read_messages())
        self.stderr_reader = asyncio.create_task(self._read_stderr())
        try:
            await self.request("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "boxagent-python", "version": "0.1.0"},
            })
            await self._send({"method": "notifications/initialized"})
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def _send(self, message: dict):
        if not self.process or self.process.returncode is not None:
            raise RuntimeError("Computer Use 客户端未运行")
        async with self.write_lock:
            if os.environ.get("COMPUTER_USE_DEBUG"):
                print("发送", message.get("id"), message.get("method"), file=sys.stderr)
            self.process.stdin.write((json.dumps(
                {"jsonrpc": "2.0", **message}, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
            await self.process.stdin.drain()

    async def request(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        except (TimeoutError, asyncio.CancelledError):
            # 取消通知不保证撤销已经发出的界面动作。
            with contextlib.suppress(Exception):
                await self._send({"method": "notifications/cancelled", "params": {
                    "requestId": request_id, "reason": "调用方超时或取消",
                }})
            raise
        finally:
            self.pending.pop(request_id, None)

    async def _handle_callback(self, message: dict):
        response = {"id": message["id"]}
        if message["method"] == "elicitation/create":
            accepted = False
            params = message.get("params", {})
            schema = params.get("requestedSchema", {})
            if self.approve and schema.get("properties") == {}:
                try:
                    accepted = await self.approve(params)
                except Exception:
                    accepted = False
            # 不提交持久化授权；非空表单需要宿主自行扩展处理。
            response["result"] = ({"action": "accept", "content": {}}
                                  if accepted else {"action": "decline"})
        else:
            response["error"] = {"code": -32601, "message": "不支持的宿主回调"}
        with contextlib.suppress(ConnectionError, RuntimeError):
            await self._send(response)

    async def _read_messages(self):
        error = RuntimeError("Computer Use 客户端已断开")
        try:
            while line := await self.process.stdout.readline():
                if not line.strip():
                    continue
                message = json.loads(line)
                if os.environ.get("COMPUTER_USE_DEBUG"):
                    print("接收", message.get("id"), message.get("method"), file=sys.stderr)
                if "method" in message and "id" in message:
                    task = asyncio.create_task(self._handle_callback(message))
                    self.callbacks.add(task)
                    task.add_done_callback(self.callbacks.discard)
                elif (future := self.pending.get(message.get("id"))) is not None:
                    if not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(json.dumps(
                                message["error"], ensure_ascii=False)))
                        else:
                            future.set_result(message.get("result", {}))
        except Exception as exc:
            error = exc
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)

    async def _read_stderr(self):
        while data := await self.process.stderr.read(4096):
            sys.stderr.write(data.decode(errors="replace"))

    async def list_tools(self) -> list[dict]:
        tools, params = [], {}
        while True:
            result = await self.request("tools/list", params)
            tools.extend(result.get("tools", []))
            if not result.get("nextCursor"):
                return tools
            params = {"cursor": result["nextCursor"]}

    async def call(self, tool: str, **arguments) -> dict:
        result = await self.request("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            text = self.text(result)
            if "Sender process is not authenticated" in text:
                raise ComputerUseAuthenticationError(
                    "Computer Use 拒绝了调用进程的认证。MCP 握手成功不代表宿主获准操作 App；"
                    "这不是计算器授权被拒绝。原始错误：" + text
                )
            raise RuntimeError(text)
        return result

    async def get_app_state(self, app: str) -> dict:
        return await self.call("get_app_state", app=app)

    @staticmethod
    def text(result: dict) -> str:
        return "\n".join(item["text"] for item in result.get("content", [])
                         if item.get("type") == "text")

    async def close(self):
        for task in list(self.callbacks):
            task.cancel()
        if self.callbacks:
            await asyncio.gather(*self.callbacks, return_exceptions=True)
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                await self.process.wait()
        if self.reader:
            await self.reader
        if self.stderr_reader:
            await self.stderr_reader


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="实际操作计算器计算 137+248")
    parser.add_argument("--allow-calculator-once", action="store_true",
                        help="确认允许本次计算器操作，不保存永久许可")
    args = parser.parse_args()
    if args.demo and not args.allow_calculator_once:
        parser.error("计算器演示需要 --allow-calculator-once 明确授权")

    async def approve(params: dict) -> bool:
        allowed = (args.allow_calculator_once
                   and params.get("message") == "Allow ChatGPT to use Calculator?"
                   and params.get("requestedSchema", {}).get("properties") == {})
        print(json.dumps({"授权问题": params.get("message"), "本次接受": allowed}, ensure_ascii=False))
        return allowed

    started = time.monotonic()
    async with ComputerUse(approve=approve) as computer:
        tools = await computer.list_tools()
        print("MCP 握手成功，工具：" + ", ".join(tool["name"] for tool in tools))
        if not args.demo:
            return
        app = "com.apple.calculator"
        state = await computer.get_app_state(app)
        match = re.search(r"^\s*(\d+) 按钮 Description: (?:全部清除|清除),",
                          computer.text(state), re.MULTILINE)
        if not match:
            raise RuntimeError("当前界面未找到清除按钮，停止操作")
        await computer.call("click", app=app, element_index=match[1])
        await computer.get_app_state(app)
        await computer.call("type_text", app=app, text="137+248")
        await computer.get_app_state(app)
        await computer.call("press_key", app=app, key="Return")
        result = await computer.get_app_state(app)
        text = computer.text(result)
        normalized = re.sub(r"[\u200e\u200f\u2066-\u2069]", "", text)
        passed = bool(re.search(r"^\s*\d+ 文本 385\s*$", normalized, re.MULTILINE))
        output = Path(__file__).resolve().parents[1] / ".runtime/private/artifacts/computer-use/python"
        output.mkdir(parents=True, exist_ok=True)
        (output / "result.txt").write_text(text, encoding="utf-8")
        for item in result.get("content", []):
            if item.get("type") == "image":
                suffix = "png" if item["mimeType"] == "image/png" else "jpg"
                (output / f"result.{suffix}").write_bytes(base64.b64decode(item["data"]))
        summary = {"算式": "137+248", "预期结果": "385", "界面核验通过": passed,
                   "总耗时秒": round(time.monotonic() - started, 3)}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False))
        print(f"结果保存在：{output}")
        if not passed:
            raise RuntimeError("界面结果与预期不符")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except TimeoutError:
        sys.exit("Computer Use 请求超时；不能仅凭超时判断是否为认证问题。当前 Python 直连尚未通过界面读取验证。")
    except (RuntimeError, FileNotFoundError) as error:
        sys.exit(str(error))
