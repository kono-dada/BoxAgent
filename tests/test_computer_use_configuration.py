from pathlib import Path

import pytest

from boxagent.core.errors import TaskFailure
from boxagent.infrastructure.runtimes.codex.computer_use import resolve_computer_use_client


def test_windows_missing_executor_reports_actionable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOXAGENT_COMPUTER_USE_CLIENT", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    with pytest.raises(TaskFailure, match="Windows 电脑操作执行器") as error:
        resolve_computer_use_client()
    assert error.value.code == "computer_use_unavailable"


def test_explicit_executor_is_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "client.exe"
    executable.touch()
    monkeypatch.setenv("BOXAGENT_COMPUTER_USE_CLIENT", str(executable))
    assert resolve_computer_use_client() == executable.resolve()


def test_missing_explicit_executor_reports_configuration_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOXAGENT_COMPUTER_USE_CLIENT", str(tmp_path / "missing.exe"))
    with pytest.raises(TaskFailure, match="执行器不存在") as error:
        resolve_computer_use_client()
    assert error.value.code == "computer_use_unavailable"
