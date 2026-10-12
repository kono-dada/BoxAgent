"""Launch installed panel assets without running development build tools."""

import os
import subprocess
from pathlib import Path


def launch_panel(root: Path, data_dir: Path, log_dir: Path) -> subprocess.Popen[bytes]:
    panel = root / "web/panel"
    executable = panel / "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
    if not executable.is_file() or not (panel / "dist/index.html").is_file():
        raise RuntimeError("请先运行 ./scripts/install.sh 安装并构建伙伴控制台。")
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "panel.log").open("ab") as output:
        return subprocess.Popen(
            [str(executable), str(panel)],
            env={**os.environ, "BOXAGENT_DATA_DIR": str(data_dir)},
            stdout=output, stderr=output)
