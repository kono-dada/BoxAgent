"""历史预实验接口：默认限定计算器；当前桌宠使用 boxagent.executor。"""

import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil

from computer_use import ApprovalHandler, ComputerUse


class CodexComputerUse(ComputerUse):
    """保持一个 App Server 会话；操作由调用方决定，不调用模型。"""

    def __init__(self, *, approve: ApprovalHandler | None = None,
                 codex_bin: str | None = None,
                 allowed_apps: set[str] | None = None, timeout: float = 30):
        super().__init__(approve=approve, timeout=timeout)
        self.codex_bin = codex_bin
        self.allowed_apps = frozenset({"com.apple.calculator"} if allowed_apps is None else allowed_apps)
        self.thread_id = None

    async def __aenter__(self):
        root = Path(__file__).resolve().parents[1]
        pinned = root / ".runtime/codex-0.153.0/codex"
        executable = shutil.which(self.codex_bin or str(pinned))
        if not executable:
            raise FileNotFoundError("找不到已验证的 Codex 运行时；请通过 codex_bin 指定路径")
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        client = codex_home / "computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
        if not client.is_file():
            raise FileNotFoundError(f"找不到 Computer Use 执行器：{client}")
        config = 'mcp_servers.boxagent_cua={command=' + json.dumps(str(client)) + ',args=["mcp"],enabled_tools=["get_app_state","click","type_text","press_key","scroll","set_value","select_text","drag","perform_secondary_action"]}'
        env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "CODEX_HOME") if key in os.environ}
        self.process = await asyncio.create_subprocess_exec(
            executable, "app-server", "--stdio", "-c", config, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=64 * 1024 * 1024)
        self.reader = asyncio.create_task(self._read_messages())
        self.stderr_reader = asyncio.create_task(self._read_stderr())
        try:
            await self.request("initialize", {"clientInfo": {"name": "boxagent-python", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            await self._send({"method": "initialized"})
            result = await self.request("thread/start", {"cwd": str(root), "ephemeral": True, "approvalPolicy": "on-request", "sandbox": "read-only"})
            self.thread_id = result["thread"]["id"]
        except BaseException:
            await self.close()
            raise
        return self

    async def _handle_callback(self, message):
        # App Server 的授权回调名称与原始 MCP 不同。
        accepted = False
        params = message.get("params", {})
        if message["method"] == "mcpServer/elicitation/request":
            if (params.get("serverName") == "boxagent_cua" and self.approve
                    and params.get("requestedSchema", {}).get("properties") == {}):
                try:
                    accepted = await self.approve(params)
                except Exception:
                    accepted = False
            result = {"action": "accept", "content": {}} if accepted else {"action": "decline"}
            await self._send({"id": message["id"], "result": result})
        else:
            await self._send({"id": message["id"], "error": {"code": -32601, "message": "桌宠尚未实现该回调"}})

    async def request(self, method, params):
        # App Server 不使用原始 MCP 的 notifications/cancelled 协议。
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, self.timeout)
        except (TimeoutError, asyncio.CancelledError):
            # 终止本会话，避免继续发出新动作；已经发出的动作不保证撤销。
            with contextlib.suppress(Exception):
                await self.close()
            raise
        finally:
            self.pending.pop(request_id, None)

    async def list_tools(self):
        result = await self.request("mcpServerStatus/list", {})
        server = next(s for s in result["data"] if s["name"] == "boxagent_cua")
        return list(server["tools"].values())

    async def call(self, tool, **arguments):
        if arguments.get("app") not in self.allowed_apps:
            raise ValueError("目标 App 不在本次桌宠会话的允许范围内")
        result = await self.request("mcpServer/tool/call", {"threadId": self.thread_id,
            "server": "boxagent_cua", "tool": tool, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(self.text(result))
        return result
