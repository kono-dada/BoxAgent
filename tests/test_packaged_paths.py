from pathlib import Path

from boxagent.bootstrap.settings import load_settings


def test_packaged_paths_keep_mutable_data_outside_resources(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / 'User Data'
    monkeypatch.setenv('BOXAGENT_PACKAGED', '1')
    monkeypatch.setenv('BOXAGENT_DATA_DIR', str(data))
    monkeypatch.delenv('BOXAGENT_JEV_MEM_PYTHON', raising=False)
    monkeypatch.delenv('BOXAGENT_PERCEPTION_PYTHON', raising=False)
    settings = load_settings()
    assert settings.jev_mem_python.is_relative_to(data)
    assert settings.perception_python.is_relative_to(data)
    assert settings.soul_file == settings.root / 'assets/personas/default/SOUL.md'


def test_packaged_mode_does_not_read_source_env_file(monkeypatch) -> None:
    from boxagent.bootstrap import settings
    monkeypatch.setenv('BOXAGENT_PACKAGED', '1')
    monkeypatch.delenv('NONEXISTENT_TEST_SECRET', raising=False)
    assert settings.read_optional_secret('NONEXISTENT_TEST_SECRET') == ''


def test_startup_memory_probe_uses_the_selected_dashscope_decision_service(monkeypatch) -> None:
    import os
    from boxagent.bootstrap import startup

    monkeypatch.setenv('BOXAGENT_PACKAGED', '1')
    monkeypatch.setenv('BOXAGENT_DECISION_PROVIDER', 'auto')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-local-key')
    monkeypatch.setenv('BOXAGENT_DASHSCOPE_WORKSPACE_ID', 'test-workspace')
    monkeypatch.setenv('BOXAGENT_DASHSCOPE_REGION', 'cn-beijing')
    for name in ('TYPESAFE_API_KEY', 'TYPESAFE_BASE_URL', 'TYPESAFE_DEFAULT_MODEL'):
        monkeypatch.delenv(name, raising=False)
    observed = {}
    monkeypatch.setattr(startup, 'run_probe', lambda: observed.update({
        key: os.environ.get(key) for key in ('TYPESAFE_API_KEY', 'TYPESAFE_BASE_URL', 'TYPESAFE_DEFAULT_MODEL')
    }))
    startup.main()
    assert observed['TYPESAFE_API_KEY'] == 'test-local-key'
    assert observed['TYPESAFE_BASE_URL'] == 'https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode'
    assert observed['TYPESAFE_DEFAULT_MODEL'] == 'decision-model-preview'
