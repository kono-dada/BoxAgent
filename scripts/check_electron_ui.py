"""Run the isolated Electron desktop fixture without cloud or device actions."""
import subprocess
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    return subprocess.run(["pnpm", "--dir", "web/panel", "exec", "electron",
                           "tests/desktop-smoke.cjs"], cwd=root).returncode
