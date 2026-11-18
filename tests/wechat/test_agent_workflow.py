"""Capabilities reach the conversational entry point, not just task tools."""
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from boxagent.agent.harness.wechat_tools import WECHAT_FRONT_INSTRUCTIONS
from boxagent.bootstrap.engine import create_voice_factory
from boxagent.bootstrap.settings import load_settings


@pytest.mark.parametrize("transport", ["websocket", "aoq"])
@pytest.mark.parametrize("enabled", [True, False])
def test_voice_entry_advertises_only_configured_wechat(
    tmp_path: Path, transport: str, enabled: bool,
) -> None:
    settings = replace(load_settings(), log_dir=tmp_path / "logs",
                       qwen_transport=transport, wechat_enabled=enabled,
                       qwen_api_key="fixture-key", aoq_output_device="")
    with patch("boxagent.infrastructure.runtimes.qwen.aoq.aoq_preflight", return_value=(True, "")):
        factory = create_voice_factory(settings)
    instructions = factory.keywords["instructions"]
    assert isinstance(instructions, str)
    assert (WECHAT_FRONT_INSTRUCTIONS in instructions) is enabled
    assert "run_task" in instructions
