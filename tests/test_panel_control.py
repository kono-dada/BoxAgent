"""Electron resource service: ownership and persistence after renderer readiness."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from boxagent.interfaces.desktop.service import DesktopService
from boxagent.interfaces.desktop.pets.catalog import PetCatalog


@pytest.fixture
def service(tmp_path: Path):
    bundled = tmp_path / "models"
    avatar = bundled / "one"
    avatar.mkdir(parents=True)
    (avatar / "pet.json").write_text(json.dumps({"type": "vrm", "version": 1, "model": "model.vrm", "actions": {}}))
    (avatar / "model.vrm").write_bytes(b"model fixture")
    soul = tmp_path / "SOUL.md"
    soul.write_text("名字：伙伴\n\n保持温柔。\n")
    result = DesktopService(backend=Mock(), catalog=PetCatalog(tmp_path / "catalog", default_pet=avatar, bundled_root=bundled), data_dir=tmp_path, log_dir=tmp_path / "logs", soul_file=soul, directory=avatar)
    yield result
    result.close()


def test_persona_invalid_save_preserves_content(service):
    original = service.soul_file.read_bytes()
    with pytest.raises(ValueError):
        service.dispatch("desktop_save_persona", {"name": " ", "content": "overwrite"})
    assert service.soul_file.read_bytes() == original
    result = service.dispatch("desktop_save_persona", {"name": "小芽", "content": "名字：伙伴\n\n保持温柔。"})
    assert result["name"] == "小芽"
    assert "保持温柔" in service.soul_file.read_text()


def test_avatar_selection_commits_only_after_valid_token(service):
    selected = service.catalog.root / "selected.json"
    prepared = service.dispatch("desktop_prepare_avatar", {"id": str(service.directory)})
    assert not selected.exists()
    service.dispatch("desktop_cancel_avatar", {"token": prepared["token"]})
    with pytest.raises(ValueError):
        service.dispatch("desktop_commit_avatar", {"token": prepared["token"]})
    assert not selected.exists()
    prepared = service.dispatch("desktop_prepare_avatar", {"id": str(service.directory)})
    service.dispatch("desktop_commit_avatar", {"token": prepared["token"]})
    assert json.loads(selected.read_text())["id"] == "one"
    with pytest.raises(ValueError):
        service.dispatch("desktop_prepare_avatar", {"id": "/tmp/untrusted"})


def test_scale_rejects_invalid_values_without_persistence(service):
    for value in (True, "2", None, 0, 6.01, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            service.dispatch("desktop_set_pet_scale", {"scale": value})
    assert not (service.data_dir / "appearance.json").exists()
    result = service.dispatch("desktop_set_pet_scale", {"scale": 2.37})
    assert result["scale"] == 2.37
    assert json.loads((service.data_dir / "appearance.json").read_text())["scale"] == 2.37


def test_run_directory_requires_matching_session(service):
    directory = service.data_dir / "sessions/ses_one/tasks/abc123abc123"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text(json.dumps({"session_id": "ses_one", "task_id": "abc123abc123", "started_at": 1}))
    assert service.dispatch("desktop_runs", {"session_id": "ses_one"})["items"][0]["id"] == str(directory)
    assert service.dispatch("desktop_run_directory", {"session_id": "ses_one", "id": str(directory)})["path"] == str(directory)
    with pytest.raises(ValueError):
        service.dispatch("desktop_run_directory", {"session_id": "ses_two", "id": str(directory)})


def test_shutdown_closes_engine_and_resources_once(service):
    service.dispatch("desktop_preview_avatar", {"id": str(service.directory)})
    resources = list(service.servers.values())
    service.close()
    service.close()
    service.backend.close.assert_called_once()
    assert all(not item.thread.is_alive() for item in resources)
    with pytest.raises(RuntimeError):
        service.dispatch("desktop_snapshot", {})
