"""Launch the installed Electron host using the active Python runtime."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol, cast

from boxagent.bootstrap.settings import Settings


class _RunningApplication(Protocol):
    def isTerminated(self) -> bool: ...
    def terminate(self) -> bool: ...
    def forceTerminate(self) -> bool: ...


def _launch_macos(executable: Path, panel: Path, environment: dict[str, str]) -> int:
    # LaunchServices gives this app its own TCC responsibility. Direct Popen
    # inherits a terminal/IDE's responsibility; Speech can then abort because
    # that parent app has no speech-recognition usage description.
    from AppKit import NSRunningApplication, NSWorkspace, NSWorkspaceOpenConfiguration
    from Foundation import NSURL, NSDate, NSRunLoop

    applications: list[_RunningApplication] = []
    errors: list[str] = []

    def completed(application: object, error: object) -> None:
        if error is not None:
            errors.append(str(error))
        elif isinstance(application, NSRunningApplication):
            applications.append(cast(_RunningApplication, application))
        else:
            errors.append("LaunchServices 未返回有效应用进程")

    config = NSWorkspaceOpenConfiguration.configuration()
    config.setArguments_([str(panel), "--desktop"])
    config.setEnvironment_(environment)
    config.setCreatesNewApplicationInstance_(True)
    NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_(
        NSURL.fileURLWithPath_(str(executable.parents[2])), config, completed)

    def poll() -> None:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.1))

    deadline = time.monotonic() + 30
    while not applications and not errors and time.monotonic() < deadline:
        poll()
    if not applications:
        raise RuntimeError(
            "BoxAgent 应用启动失败：" + (errors[0] if errors else "LaunchServices 超时")
        )
    application = applications[0]
    try:
        while not application.isTerminated():
            poll()
        return 0
    except KeyboardInterrupt:
        application.terminate()
        deadline = time.monotonic() + 20
        while not application.isTerminated() and time.monotonic() < deadline:
            poll()
        if not application.isTerminated():
            application.forceTerminate()
        return 130


def run_electron(settings: Settings, arguments: list[str]) -> int:
    panel = settings.root / "web/panel"
    if sys.platform not in {"darwin", "win32"}:
        raise RuntimeError(f"不支持的桌面操作系统：{sys.platform}")
    executable = (
        panel
        / "node_modules/electron/dist"
        / ("electron.exe" if sys.platform == "win32" else "Electron.app/Contents/MacOS/Electron")
    )
    if not executable.is_file() or not all(
        (panel / "dist" / name).is_file() for name in ("index.html", "companion.html", "pet.html")
    ):
        raise RuntimeError(
            "请先运行 scripts/install.ps1（Windows）或 ./scripts/install.sh（macOS）安装并构建 "
            "Electron 桌宠。"
        )
    prepared = subprocess.run(
        ["node", str(panel / "scripts/launch.cjs"), "--prepare"],
        cwd=settings.root,
        check=True,
        capture_output=True,
        text=True,
    )
    executable = Path(prepared.stdout.strip())
    if not executable.is_file():
        raise RuntimeError("BoxAgent 桌面运行环境准备失败，请重新运行安装入口。")
    environment = {**os.environ, "BOXAGENT_DATA_DIR": str(settings.data_dir),
                   "BOXAGENT_DESKTOP_PYTHON": sys.executable,
                   "BOXAGENT_DESKTOP_ARGS": json.dumps(arguments)}
    if sys.platform == "darwin":
        return _launch_macos(executable, panel, environment)
    child = subprocess.Popen([str(executable), str(panel), "--desktop"], cwd=settings.root,
                             env=environment)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        return child.wait(timeout=20)
