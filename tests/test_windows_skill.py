import asyncio
import json
from pathlib import Path
import tomllib
from unittest.mock import AsyncMock, Mock

import pytest

from boxagent.core.errors import TaskFailure
from boxagent.infrastructure.runtimes.codex.app_server import CodexAppServer
from boxagent.infrastructure.runtimes.codex.windows_skill import (
    WindowsSkill, load_windows_skill, refresh_pipe_environment,
)


def test_stale_pipe_refresh_is_local_and_preserves_permissions() -> None:
    name = 'codex-computer-use-11111111-1111-1111-1111-111111111111'
    environment = {'SKY_CUA_NATIVE_PIPE': '1', 'SKY_CUA_NATIVE_PIPE_DIRECTORY': 'stale',
                   'NODE_REPL_TRUSTED_SERVICES': 'unchanged'}
    refreshed = refresh_pipe_environment(environment, lambda: (name,))
    assert refreshed['SKY_CUA_NATIVE_PIPE_DIRECTORY'] == '\\\\.\\pipe\\' + name
    assert environment['SKY_CUA_NATIVE_PIPE_DIRECTORY'] == 'stale'
    assert refreshed['NODE_REPL_TRUSTED_SERVICES'] == 'unchanged'


@pytest.mark.parametrize('names', [(), (
    'codex-computer-use-11111111-1111-1111-1111-111111111111',
    'codex-computer-use-22222222-2222-2222-2222-222222222222'), ('unrelated',)])
def test_stale_pipe_rejects_missing_or_ambiguous_services(names: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match='通信管道'):
        refresh_pipe_environment({'SKY_CUA_NATIVE_PIPE': '1'}, lambda: names)


def test_live_pipe_is_preserved_when_multiple_services_exist() -> None:
    first = 'codex-computer-use-11111111-1111-1111-1111-111111111111'
    second = 'codex-computer-use-22222222-2222-2222-2222-222222222222'
    environment = {'SKY_CUA_NATIVE_PIPE': '1',
                   'SKY_CUA_NATIVE_PIPE_DIRECTORY': '\\\\.\\pipe\\' + second}
    assert refresh_pipe_environment(environment, lambda: (first, second)) == environment


def test_loader_refreshes_task_override_without_rewriting_global_config(installed_skill: WindowsSkill) -> None:
    home = Path(installed_skill.environment['CODEX_HOME'])
    config = home / 'config.toml'
    text = config.read_text(encoding='utf-8') + (
        '\nSKY_CUA_NATIVE_PIPE="1"\nSKY_CUA_NATIVE_PIPE_DIRECTORY="stale"\n')
    config.write_text(text, encoding='utf-8')
    name = 'codex-computer-use-11111111-1111-1111-1111-111111111111'
    skill = load_windows_skill(home, pipe_names=lambda: (name,))
    override = tomllib.loads(skill.config_override())['mcp_servers']['node_repl']['env']
    assert override['SKY_CUA_NATIVE_PIPE_DIRECTORY'].endswith(name)
    assert config.read_text(encoding='utf-8') == text


@pytest.fixture
def installed_skill(tmp_path: Path) -> WindowsSkill:
    command = tmp_path / "node repl.exe"
    command.touch()
    skill = tmp_path / "plugins/cache/openai-bundled/computer-use/1/skills/computer-use/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("import @oai/sky", encoding="utf-8")
    docs = skill.parent.parent.parent / "docs"
    docs.mkdir()
    for name in ("guidance", "api", "confirmations"):
        (docs / f"{name}.md").write_text(f"{name} instructions", encoding="utf-8")
    (tmp_path / "config.toml").write_text(
        '[mcp_servers.node_repl]\ncommand=' + json.dumps(str(command))
        + '\nargs=[]\nenv_vars=["SYSTEMROOT"]\nstartup_timeout_sec=120\n'
        + '[mcp_servers.node_repl.env]\nCODEX_HOME=' + json.dumps(str(tmp_path)),
        encoding="utf-8")
    return load_windows_skill(tmp_path)


def make_server(tmp_path: Path, skill: WindowsSkill) -> CodexAppServer:
    server = CodexAppServer(
        output=tmp_path, workspace=tmp_path, on_tool_call=AsyncMock(),
        approve=AsyncMock(), current_app=lambda: "", report=Mock(), log=Mock())
    server.windows_skill = skill
    return server


def test_skill_configuration_round_trips_without_modifying_native_home(installed_skill: WindowsSkill) -> None:
    config = tomllib.loads(installed_skill.config_override())["mcp_servers"]["node_repl"]
    assert config["command"] == str(installed_skill.command)
    assert config["env"] == installed_skill.environment
    assert config["env_vars"] == ["SYSTEMROOT"]
    assert all(name in installed_skill.instructions for name in ("guidance", "api", "confirmations"))


def test_missing_skill_reports_setup_failure(tmp_path: Path) -> None:
    with pytest.raises(TaskFailure) as error:
        load_windows_skill(tmp_path)
    assert error.value.code == "windows_skill_unavailable"


def test_disabled_node_tool_is_respected(installed_skill: WindowsSkill) -> None:
    home = Path(installed_skill.environment["CODEX_HOME"])
    config = home / "config.toml"
    text = config.read_text(encoding="utf-8").replace('args=[]', 'args=[]\ndisabled_tools=["js"]')
    config.write_text(text, encoding="utf-8")
    with pytest.raises(TaskFailure) as error:
        load_windows_skill(home)
    assert "js 工具已被禁用" in error.value.detail


def test_windows_launch_uses_registered_node_server(installed_skill: WindowsSkill, tmp_path: Path) -> None:
    server = make_server(tmp_path, installed_skill)
    arguments = server.launch_arguments(tmp_path / "codex.exe", installed_skill.command)
    assert installed_skill.config_override() in arguments
    assert not any("boxagent_cua=" in argument for argument in arguments)
    assert not any("turn-ended" in argument for argument in arguments)


def test_windows_tool_routing_and_cleanup(installed_skill: WindowsSkill, tmp_path: Path) -> None:
    async def check() -> None:
        server = make_server(tmp_path, installed_skill)
        server.thread_id, server.turn_id = "thread", "turn"
        rpc = AsyncMock(return_value={"content": [{"type": "text", "text": "observed"}]})
        server.rpc = rpc
        await server.call_tool("windows_js", {"code": "await sky.list_apps()"})
        assert rpc.call_args.args[1]["server"] == "node_repl"
        assert rpc.call_args.args[1]["tool"] == "js"
        await server.end_turn()
        assert rpc.call_args.args[1]["tool"] == "turn_ended"
        assert rpc.call_args.args[1]["arguments"]["turn_id"] == "turn"
        assert server.cleanup_status == "notified"
        assert server.turn_id is None
    asyncio.run(check())


def test_windows_cleanup_error_is_not_reported_as_success(installed_skill: WindowsSkill, tmp_path: Path) -> None:
    async def check() -> None:
        server = make_server(tmp_path, installed_skill)
        server.thread_id, server.turn_id = "thread", "turn"
        server.rpc = AsyncMock(return_value={"isError": True})
        await server.end_turn()
        assert server.cleanup_status == "failed"
    asyncio.run(check())


def test_native_node_results_are_recorded_as_windows_evidence(installed_skill: WindowsSkill, tmp_path: Path) -> None:
    async def check() -> None:
        server = make_server(tmp_path, installed_skill)
        server.thread_id, server.turn_id = "thread", "turn"
        server.tools = [{"name": "desktop_windows_js"}]
        sink = AsyncMock()
        server.on_tool_observed = sink
        item = {"id": "call-1", "type": "mcpToolCall", "server": "node_repl", "tool": "js",
                "arguments": {"code": "await sky.list_apps()"}}
        await server.observe_native_tool_event("item/started", {"turnId": "turn", "item": item})
        response = {"content": [{"type": "text", "text": "Calculator window observed"}]}
        await server.observe_native_tool_event("item/completed", {
            "turnId": "turn", "item": {**item, "status": "completed", "result": response}})
        assert sink.call_args.args[0] == "windows_js"
        assert sink.call_args.args[2]["content"] == response["content"]
    asyncio.run(check())
