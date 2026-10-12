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
    executable = panel / "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
    if not executable.is_file() or not all((panel / "dist" / name).is_file()
                                          for name in ("index.html", "companion.html", "pet.html")):
        raise RuntimeError("请先运行 ./scripts/install.sh 安装并构建 Electron 桌宠。")
    child = subprocess.Popen([str(executable), str(panel), "--desktop"], cwd=settings.root,
        env={**os.environ, "BOXAGENT_DATA_DIR": str(settings.data_dir),
             "BOXAGENT_DESKTOP_PYTHON": sys.executable,
             "BOXAGENT_DESKTOP_ARGS": json.dumps(arguments)})
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        return child.wait(timeout=20)
