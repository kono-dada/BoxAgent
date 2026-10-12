"""Menu launches installed assets and reports incomplete installations."""

import json
from pathlib import Path

import pytest

from boxagent.interfaces.macos.panel_launch import launch_panel


def test_menu_launch_runs_installed_binary_without_node_or_pnpm(tmp_path: Path, monkeypatch):
    panel = tmp_path / "web/panel"
    executable = panel / "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
    executable.parent.mkdir(parents=True)
    executable.write_text('#!/bin/sh\nprintf "%s\\n" "$1" "$BOXAGENT_DATA_DIR"\n')
    executable.chmod(0o700)
    (panel / "dist").mkdir()
    (panel / "dist/index.html").write_text("fixture")
    monkeypatch.setenv("PATH", "/nonexistent")
    process = launch_panel(tmp_path, tmp_path / "data", tmp_path / "diagnostics")
    assert process.wait(timeout=5) == 0
    assert (tmp_path / "diagnostics/panel.log").read_text().splitlines() == [
        str(panel), str(tmp_path / "data")]


def test_missing_build_reports_installation_before_spawning(tmp_path: Path):
    with pytest.raises(RuntimeError, match="安装并构建"):
        launch_panel(tmp_path, tmp_path / "data", tmp_path / "diagnostics")
    assert not (tmp_path / "diagnostics").exists()


def test_start_command_does_not_rebuild_but_dev_does():
    scripts = json.loads((Path(__file__).resolve().parents[1] / "web/panel/package.json").read_text())["scripts"]
    assert scripts["start"] == "electron ."
    assert "build" in scripts["dev"]
