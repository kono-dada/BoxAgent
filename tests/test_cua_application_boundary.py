"""WeChat tasks have no native CU; the dynamic desktop gateway also rejects it."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from boxagent.agent.harness.policies.tools import ComputerUseToolGateway
from boxagent.infrastructure.runtimes.codex.app_server import CodexAppServer


def test_wechat_runtime_has_no_mcp_cu_or_shell(tmp_path: Path) -> None:
    server = CodexAppServer(output=tmp_path, on_tool_call=AsyncMock(), approve=lambda _: False,
        current_app=lambda: "", report=lambda *_: None, log=lambda *_args, **_kwargs: None,
        computer_use_enabled=False)
    arguments = server.launch_arguments(tmp_path / "codex", None)
    assert "mcp_servers={}" in arguments
    assert "features.shell_tool=false" in arguments
    assert not any("boxagent_cua" in argument for argument in arguments)


def test_dynamic_tools_are_installed_on_a_fresh_thread_after_restart(tmp_path: Path) -> None:
    server = CodexAppServer(output=tmp_path, on_tool_call=AsyncMock(), approve=lambda _: False,
        current_app=lambda: "", report=lambda *_: None, log=lambda *_args, **_kwargs: None,
        computer_use_enabled=False)
    server.tools = [{"type": "function", "name": "wechat_unread", "description": "未读",
                     "inputSchema": {"type": "object", "properties": {}}}]
    server.rpc = AsyncMock(return_value={"thread": {"id": "fresh"}})
    result = asyncio.run(server.ensure_thread(model="fixture", instructions="fixture",
        preferred_thread_id="old-without-wechat", product_session_id="s"))
    assert result == ("fresh", "started")
    method, params = server.rpc.await_args.args
    assert method == "thread/start"
    assert params["dynamicTools"] == server.tools


@pytest.mark.parametrize("app", ["微信", "WeChat", "WeChat-BoxAgent", "com.tencent.xinWeChat"])
def test_dynamic_gateway_rejects_wechat(tmp_path: Path, app: str) -> None:
    session = AsyncMock()
    gateway = ComputerUseToolGateway(session, output=tmp_path, goal="读取微信", report=lambda *_: None,
        log=lambda *_args, **_kwargs: None, is_stopped=lambda: False)
    gateway.bind([{"name": "get_app_state", "inputSchema": {"type": "object", "properties": {"app": {}},
        "required": ["app"]}}])
    result = asyncio.run(gateway.call("desktop_get_app_state", {"app": app}))
    assert result["success"] is False
    session.call_tool.assert_not_awaited()
    assert "wechat_cu_forbidden" in result["contentItems"][0]["text"]
