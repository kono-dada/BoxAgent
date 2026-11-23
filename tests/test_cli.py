"""Exercise both installed entrypoints outside the checkout."""

import os
import subprocess
import sys
import sysconfig
from importlib.metadata import distribution
from pathlib import Path

from boxagent import __main__
from boxagent.entrypoints.desktop import main


def test_installed_boxagent_entrypoints(tmp_path: Path) -> None:
    package = distribution("boxagent")
    assert package.entry_points["boxagent"].load() is main
    assert __main__.main is main
    assert Path(__main__.__file__).with_name("py.typed").is_file()

    name = "boxagent.exe" if os.name == "nt" else "boxagent"
    console = Path(sysconfig.get_path("scripts")) / name
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    for command in ([str(console)], [sys.executable, "-m", "boxagent"]):
        result = subprocess.run(
            [*command, "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        assert "BoxAgent 桌宠" in result.stdout
        assert "--task-provider" in result.stdout
