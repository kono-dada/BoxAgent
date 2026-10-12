"""The Python entrypoint launches one installed Electron desktop host."""
import json
from pathlib import Path
from dataclasses import replace
import pytest
from boxagent.bootstrap.settings import load_settings
from boxagent.bootstrap.electron_launch import run_electron


def test_launch_passes_runtime_and_arguments_without_shell(tmp_path: Path, monkeypatch):
    panel = tmp_path / "web/panel"
    executable = panel / "node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
    executable.parent.mkdir(parents=True)
    record = tmp_path / "record.json"
    executable.write_text('#!/usr/bin/env python3\nimport os,sys,json\n'
                          'from pathlib import Path\n'
                          f'Path({str(record)!r}).write_text(json.dumps([sys.argv[1:], os.environ["BOXAGENT_DESKTOP_ARGS"], os.environ["BOXAGENT_DATA_DIR"]]))\n')
    executable.chmod(0o700)
    (panel / "dist").mkdir()
    for name in ("index", "pet", "companion"):
        (panel / f"dist/{name}.html").write_text("fixture")
    settings = replace(load_settings(), root=tmp_path, data_dir=tmp_path / "data")
    assert run_electron(settings, ["--pet", "a path with spaces"]) == 0
    args, forwarded, data = json.loads(record.read_text())
    assert args == [str(panel), "--desktop"]
    assert json.loads(forwarded) == ["--pet", "a path with spaces"]
    assert data == str(settings.data_dir)


def test_missing_build_reports_installation(tmp_path: Path):
    settings = replace(load_settings(), root=tmp_path)
    with pytest.raises(RuntimeError, match="安装并构建"):
        run_electron(settings, [])


def test_start_command_does_not_rebuild_but_dev_does():
    scripts = json.loads((Path(__file__).resolve().parents[1] / "web/panel/package.json").read_text())["scripts"]
    assert scripts["start"] == "electron ."
    assert "build" in scripts["dev"]
