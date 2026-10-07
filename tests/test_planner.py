"""The task planner is one Codex runtime with selectable model profiles."""

import json
import tempfile
import unittest
from datetime import datetime
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

from boxagent.bootstrap.engine import create_application, create_task_executor
from boxagent.bootstrap.settings import load_settings
from boxagent.agent.harness.executor import TaskExecutor
from boxagent.agent.harness.request_builder import RuntimeRequestBuilder
from boxagent.infrastructure.runtimes.codex.runtime import CodexAgentRuntime
from boxagent.infrastructure.runtimes.codex.session import CodexRuntimeHost
from boxagent.agent.runtime.registry import AgentRuntimeRegistry


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_task_runs_have_date_and_time_partitioned_human_browsable_names(self):
        settings = load_settings()
        path = settings.task_run_dir(
            "abc123def456", now=datetime(2026, 10, 7, 15, 12, 6))

        self.assertEqual(path.relative_to(settings.data_dir).as_posix(),
                         "runs/2026-10-07/151206-abc123def456")
    async def test_codex_runtime_uses_native_turn_without_owning_tool_policy(self):
        session = AsyncMock()
        session.run_codex_turn.return_value = (
            '{"outcome":"blocked","summary":"需要登录","evidence_steps":[]}')
        runtime = CodexAgentRuntime(session, model="codex-test")
        tools = [{"name": "desktop_click", "inputSchema": {"type": "object"}}]
        request = RuntimeRequestBuilder().prepare("测试")
        result = await runtime.execute(request, tools, AsyncMock(), lambda _text: {}, lambda *_: None)
        self.assertEqual(session.tools, tools)
        self.assertIn("需要登录", result)
        session.run_codex_turn.assert_awaited_once()

    def test_registry_routes_both_profiles_through_codex_runtime(self):
        catalog = Path("/tmp/deepseek-models.json").resolve()
        registry = AgentRuntimeRegistry(
            task_model="codex-test",
            deepseek_model="deepseek-flash",
            runtime_factory=CodexAgentRuntime,
            deepseek_api_key="secret-for-test",
            deepseek_base_url="https://api.deepseek.com",
            deepseek_model_catalog=catalog,
        )
        session = object()
        codex = registry.runtime("codex")
        deepseek = registry.runtime("deepseek")

        self.assertIsInstance(codex.factory(session), CodexAgentRuntime)
        self.assertIsInstance(deepseek.factory(session), CodexAgentRuntime)
        self.assertEqual(codex.profile.provider, "openai")
        self.assertEqual(deepseek.profile.provider, "deepseek")
        self.assertEqual(deepseek.profile.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(deepseek.profile.model_catalog, catalog)
        self.assertNotIn("secret-for-test", repr(deepseek.profile))

    def test_factory_preserves_user_facing_provider_but_uses_one_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            app_settings = load_settings(log_dir=Path(directory))
            codex = create_task_executor("codex", provider="codex",
                                         app_settings=app_settings)
            deepseek = create_task_executor("deepseek", provider="deepseek",
                                            app_settings=app_settings)
        self.assertIsInstance(codex, TaskExecutor)
        self.assertIsInstance(deepseek, TaskExecutor)
        self.assertEqual(codex.provider, "codex")
        self.assertEqual(deepseek.provider, "deepseek")
        self.assertIsInstance(codex.runtime_factory(object()), CodexAgentRuntime)
        self.assertIsInstance(deepseek.runtime_factory(object()), CodexAgentRuntime)
        self.assertEqual(deepseek.session_factory.keywords["model_profile"].provider,
                         "deepseek")
        with self.assertRaisesRegex(ValueError, "不支持"):
            create_task_executor("other", provider="unknown", app_settings=app_settings)

    def test_deepseek_catalog_declares_flash_as_multimodal(self):
        path = Path(__file__).resolve().parents[1] / "assets/model-catalogs/deepseek.json"
        models = {item["slug"]: item for item in json.loads(path.read_text())["models"]}
        self.assertEqual(models["deepseek-flash"]["input_modalities"], ["text", "image"])
        self.assertEqual(models["deepseek-v4-pro"]["input_modalities"], ["text"])

    def test_application_tasks_share_one_runtime_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = replace(load_settings(log_dir=root / "logs"), data_dir=root / "data")
            application = create_application(
                lambda *_: None, app_settings=settings, memory_backend=None,
                voice_factory=lambda *_: None)
            first = application.execution_service.executor_factory("first")
            second = application.execution_service.executor_factory("second")

        host = application.runtime_resources[0]
        self.assertIsInstance(host, CodexRuntimeHost)
        self.assertIs(first.session_factory.__self__, host)
        self.assertIs(second.session_factory.__self__, host)
