"""Launch the installed Electron host using the active Python runtime."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from boxagent.bootstrap.settings import Settings


def run_electron(settings: Settings, arguments: list[str]) -> int:
    panel = settings.root / "web/panel"
    if sys.platform not in {"darwin", "win32"}:
        raise RuntimeError(f"不支持的桌面操作系统：{sys.platform}")
    executable = panel / "node_modules/electron/dist" / (
        "electron.exe" if sys.platform == "win32" else "Electron.app/Contents/MacOS/Electron")
    if not executable.is_file() or not all((panel / "dist" / name).is_file()
                                          for name in ("index.html", "companion.html", "pet.html")):
        raise RuntimeError("请先运行 scripts/install.ps1（Windows）或 ./scripts/install.sh（macOS）安装并构建 Electron 桌宠。")
    prepared = subprocess.run(["node", str(panel / "scripts/launch.cjs"), "--prepare"],
                              cwd=settings.root, check=True, capture_output=True, text=True)
    executable = Path(prepared.stdout.strip())
    if not executable.is_file():
        raise RuntimeError("BoxAgent 桌面运行环境准备失败，请重新运行安装入口。")
    child = subprocess.Popen([str(executable), str(panel), "--desktop"], cwd=settings.root,
        env={**os.environ, "BOXAGENT_DATA_DIR": str(settings.data_dir),
             "BOXAGENT_DESKTOP_PYTHON": sys.executable,
             "BOXAGENT_DESKTOP_ARGS": json.dumps(arguments)})
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        return child.wait(timeout=20)
