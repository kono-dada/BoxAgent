"""Locate the native Computer Use client used by the Codex Runtime."""

import os
from pathlib import Path


def resolve_computer_use_client(codex_home: str | Path | None = None) -> Path:
    root = Path(codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    client = root / (
        "computer-use/Codex Computer Use.app/Contents/SharedSupport/"
        "SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
    )
    if not client.is_file():
        raise RuntimeError("未安装 Codex Computer Use 执行器")
    return client
