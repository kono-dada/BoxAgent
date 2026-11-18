"""The Python entrypoint launches one installed Electron desktop host."""
import json
from pathlib import Path
from dataclasses import replace
import pytest
from boxagent.bootstrap.settings import load_settings
from boxagent.bootstrap.electron_launch import run_electron


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_launch_passes_runtime_and_arguments_without_shell(tmp_path: Path, monkeypatch, platform: str):
    monkeypatch.setattr("boxagent.bootstrap.electron_launch.sys.platform", platform)
    panel = tmp_path / "web/panel"
    executable = panel / "node_modules/electron/dist" / (
        "electron.exe" if __import__("sys").platform == "win32" else "Electron.app/Contents/MacOS/Electron")
    executable.parent.mkdir(parents=True)
    executable.touch()
    (panel / "dist").mkdir()
    for name in ("index", "pet", "companion"):
        (panel / f"dist/{name}.html").write_text("fixture")
    settings = replace(load_settings(), root=tmp_path, data_dir=tmp_path / "data")
    from subprocess import CompletedProcess
    monkeypatch.setattr("boxagent.bootstrap.electron_launch.subprocess.run",
                        lambda *args, **kwargs: CompletedProcess(args, 0, str(executable)))
    from unittest.mock import Mock
    child = Mock()
    child.wait.return_value = 0
    launch = Mock(return_value=child)
    monkeypatch.setattr("boxagent.bootstrap.electron_launch.subprocess.Popen", launch)
    mac_launch = Mock(return_value=0)
    monkeypatch.setattr("boxagent.bootstrap.electron_launch._launch_macos", mac_launch)
    assert run_electron(settings, ["--pet", "a path with spaces"]) == 0
    if platform == "darwin":
        launch.assert_not_called()
        assert mac_launch.call_args.args[:2] == (executable, panel)
        env = mac_launch.call_args.args[2]
    else:
        mac_launch.assert_not_called()
        assert launch.call_args.args[0] == [str(executable), str(panel), "--desktop"]
        env = launch.call_args.kwargs["env"]
    assert json.loads(env["BOXAGENT_DESKTOP_ARGS"]) == ["--pet", "a path with spaces"]
    assert env["BOXAGENT_DATA_DIR"] == str(settings.data_dir)



def test_missing_build_reports_installation(tmp_path: Path):
    settings = replace(load_settings(), root=tmp_path)
    with pytest.raises(RuntimeError, match="安装并构建"):
        run_electron(settings, [])


def test_start_command_does_not_rebuild_but_dev_does():
    scripts = json.loads((Path(__file__).resolve().parents[1] / "web/panel/package.json").read_text())["scripts"]
    assert scripts["start"] == "node scripts/launch.cjs"
    assert "build" in scripts["dev"]
