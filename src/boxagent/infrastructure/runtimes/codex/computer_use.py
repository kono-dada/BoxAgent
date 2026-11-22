"""Locate the native Computer Use client used by the Codex Runtime."""

import os
import sys
from pathlib import Path

from boxagent.core.errors import TaskFailure


def resolve_computer_use_home(codex_home: str | Path | None = None) -> Path:
    """定位原生服务的安装目录，与桌宠隔离的会话配置目录区分。"""
    return Path(codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def resolve_computer_use_client(codex_home: str | Path | None = None) -> Path:
    root = resolve_computer_use_home(codex_home)
    configured = os.environ.get("BOXAGENT_COMPUTER_USE_CLIENT")
    if configured:
        client = Path(configured).expanduser().resolve()
        if not client.is_file():
            raise TaskFailure("computer_use_unavailable", "配置的电脑操作执行器不存在，请检查 BOXAGENT_COMPUTER_USE_CLIENT",
                              detail=str(client))
        return client
    if sys.platform != "darwin":
        raise TaskFailure("computer_use_unavailable",
                          "尚未配置 Windows 电脑操作执行器，无法执行此任务。请安装兼容的 Computer Use MCP 执行器并设置 BOXAGENT_COMPUTER_USE_CLIENT，然后重启。")
    client = root / (
        "computer-use/Codex Computer Use.app/Contents/SharedSupport/"
        "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
    )
    if not client.is_file():
        raise TaskFailure("computer_use_unavailable", "未安装 Codex Computer Use 执行器")
    return client
