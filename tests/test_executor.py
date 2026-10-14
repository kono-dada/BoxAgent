"""Task kernel and Computer Use transport contracts."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from boxagent.infrastructure.runtimes.codex.app_server import (
    CodexAppServer,
    resolve_codex_runtime,
)
from boxagent.infrastructure.runtimes.codex.session import CodexRuntimeHost
from boxagent.infrastructure.runtimes.codex.runtime import CodexAgentRuntime
from boxagent.core.errors import TaskFailure, redact
from boxagent.agent.harness.executor import TaskExecutor
from boxagent.agent.harness.policies.tools import ComputerUseToolGateway, model_content
from boxagent.agent.harness.request_builder import RuntimeRequestBuilder
from boxagent.agent.runtime.models import ModelProfile, RuntimeMessage, RuntimeRequest
from boxagent.agent.runtime.results import parse_result_object
from boxagent.agent.harness.policies.result import RESULT_SCHEMA, validate_task_result
from boxagent.domain.environment import EnvironmentContext


class FakeSession:
    def __init__(self, *, output, on_tool_call, approve, current_app, report, log,
                 auto_approve=True, on_tool_observed=None):
        self.output = output
        self.on_tool_call = on_tool_call
        self.approve = approve
        self.current_app = current_app
        self.report = report
        self.log = log
        self.auto_approve = auto_approve
        self.process = None
        self.cleanup_status = "not_started"
        self.calls = []
        self.response = {"content": [{"type": "text", "text": "当前界面"}]}

    async def start(self):
        return []

    async def call_tool(self, operation, arguments):
        self.calls.append((operation, arguments))
        return self.response

    async def interrupt(self):
        pass

    async def close(self):
        self.cleanup_status = "not_needed"


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_calculator_element_indices_are_repaired_from_one_observation(self):
        case = json.loads((Path(__file__).parent / "fixtures/calculator_evidence_indices.json").read_text())
        observation = case["observations"][0]
        self.executor.observations[1] = {key: value for key, value in observation.items() if key != "text"}
        (self.executor.output / "step-1.txt").write_text(observation["text"])
        original = parse_result_object(case["original_reply"])
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result(case["original_reply"])
        self.assertEqual(failure.exception.code, "invalid_evidence")
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value=case["original_reply"])
        session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
            **original, "evidence_steps": [1]}))
        request = RuntimeRequest(case["query"], "policy", RESULT_SCHEMA,
                                 result_evidence=self.executor.result_evidence)
        tools = AsyncMock()
        text = await CodexAgentRuntime(session, model="fixture").execute(
            request, [], tools, self.executor.validate_result, lambda _: None)
        result = self.executor.validate_result(text)
        self.assertEqual(result["outcome"], original["outcome"])
        self.assertEqual(result["summary"], original["summary"])
        self.assertEqual([row["step"] for row in result["evidence"]], [1])
        sent = json.loads(session.run_isolated_structured_turn.call_args.kwargs["query"])
        self.assertEqual([row["step"] for row in json.loads(sent["current_task_observations"])], [1])
        session.run_isolated_structured_turn.assert_awaited_once()
        tools.assert_not_awaited()
        self.assertEqual(self.executor.session.calls, [])

    async def test_invalid_references_cannot_be_repaired_without_current_evidence(self):
        case = json.loads((Path(__file__).parent / "fixtures/calculator_evidence_indices.json").read_text())
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value=case["original_reply"])
        session.run_isolated_structured_turn = AsyncMock()
        with self.assertRaises(TaskFailure) as failure:
            await CodexAgentRuntime(session, model="fixture").execute(
                RuntimeRequest(case["query"], "policy", RESULT_SCHEMA), [], AsyncMock(),
                self.executor.validate_result, lambda _: None)
        self.assertEqual(failure.exception.code, "invalid_evidence")
        session.run_isolated_structured_turn.assert_not_awaited()

    async def test_invalid_reference_repair_cannot_change_claim_or_skip_verification(self):
        case = json.loads((Path(__file__).parent / "fixtures/calculator_evidence_indices.json").read_text())
        original = parse_result_object(case["original_reply"])
        self.executor.observations[1] = {"step": 1, "tool": "get_app_state", "success": True}
        for changes in ({"outcome": "needs_input"}, {"summary": "新结论"},
                        {"evidence_steps": [59]}, {"evidence_steps": []}):
            with self.subTest(changes=changes):
                session = Mock()
                session.run_codex_turn = AsyncMock(return_value=case["original_reply"])
                session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
                    **original, "evidence_steps": [1], **changes}))
                with self.assertRaises(TaskFailure):
                    await CodexAgentRuntime(session, model="fixture").execute(
                        RuntimeRequest(case["query"], "policy", RESULT_SCHEMA,
                                       result_evidence=self.executor.result_evidence), [], AsyncMock(),
                        self.executor.validate_result, lambda _: None)
                session.run_isolated_structured_turn.assert_awaited_once()

    async def test_failed_observation_cannot_validate_corrected_reference(self):
        case = json.loads((Path(__file__).parent / "fixtures/calculator_evidence_indices.json").read_text())
        original = parse_result_object(case["original_reply"])
        self.executor.observations[1] = {"step": 1, "tool": "get_app_state", "success": False}
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value=case["original_reply"])
        session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({**original, "evidence_steps": [1]}))
        with self.assertRaises(TaskFailure) as failure:
            await CodexAgentRuntime(session, model="fixture").execute(
                RuntimeRequest(case["query"], "policy", RESULT_SCHEMA,
                               result_evidence=self.executor.result_evidence), [], AsyncMock(),
                self.executor.validate_result, lambda _: None)
        self.assertEqual(failure.exception.code, "failed_evidence")

    async def test_recorded_mislabelled_clarification_requires_explicit_outcome(self):
        case = json.loads((Path(__file__).parent / "fixtures/voice_task_clarification.json").read_text())
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value=json.dumps(case["reported_result"]))
        session.run_isolated_structured_turn = AsyncMock()
        runtime = CodexAgentRuntime(session, model="fixture")
        with self.assertRaises(TaskFailure) as failure:
            await runtime.execute(RuntimeRequest(case["user_query"], "policy", RESULT_SCHEMA), [], AsyncMock(),
                                  self.executor.validate_result, lambda _: None)
        self.assertEqual(failure.exception.code, "missing_evidence")
        session.run_isolated_structured_turn.assert_not_awaited()
        # The same recorded question with an explicit needs_input outcome is
        # preserved verbatim, without heuristic language reclassification.
        session.run_codex_turn.return_value = json.dumps({**case["reported_result"], "outcome": "needs_input"})
        text = await runtime.execute(RuntimeRequest(case["user_query"], "policy", RESULT_SCHEMA), [], AsyncMock(),
                                     self.executor.validate_result, lambda _: None)
        result = self.executor.validate_result(text)
        self.assertEqual(result["summary"], case["reported_result"]["summary"])
        self.assertEqual(result["evidence"], [])
        session.run_isolated_structured_turn.assert_not_awaited()

    async def test_result_formatting_uses_actual_current_observations(self):
        self.executor.observations[8] = {"step": 8, "tool": "get_app_state", "success": True}
        (self.executor.output / "step-8.txt").write_text("Calculator: 10+10 = 20")
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value="计算器显示 10+10 = 20。")
        session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
            "outcome": "completed", "summary": "结果为 20", "evidence_steps": [8]}))
        request = RuntimeRequest("计算10加10", "policy", RESULT_SCHEMA,
                                 result_evidence=self.executor.result_evidence)
        text = await CodexAgentRuntime(session, model="fixture").execute(
            request, [], AsyncMock(), self.executor.validate_result, lambda _: None)
        self.assertEqual(self.executor.validate_result(text)["outcome"], "completed")
        sent = json.loads(session.run_isolated_structured_turn.call_args.kwargs["query"])
        self.assertIn("10+10 = 20", sent["current_task_observations"])
        self.assertEqual(json.loads(sent["current_task_observations"])[0]["step"], 8)
        self.assertEqual(self.executor.session.calls, [])

    async def test_missing_reference_repair_cannot_change_completion_or_claim(self):
        self.executor.observations[8] = {"step": 8, "tool": "get_app_state", "success": True}
        original = {"outcome": "completed", "summary": "结果为20", "evidence_steps": []}
        for changes in ({}, {"summary": "结果为30"}, {"outcome": "needs_input"}, {"evidence_steps": [99]}):
            with self.subTest(changes=changes):
                session = Mock()
                session.run_codex_turn = AsyncMock(return_value=json.dumps(original))
                session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
                    **original, "evidence_steps": [8], **changes}))
                request = RuntimeRequest("计算10加10", "policy", RESULT_SCHEMA,
                                         result_evidence=self.executor.result_evidence)
                if changes:
                    with self.assertRaises(TaskFailure):
                        await CodexAgentRuntime(session, model="fixture").execute(
                            request, [], AsyncMock(), self.executor.validate_result, lambda _: None)
                else:
                    text = await CodexAgentRuntime(session, model="fixture").execute(
                        request, [], AsyncMock(), self.executor.validate_result, lambda _: None)
                    self.assertEqual(self.executor.validate_result(text)["summary"], original["summary"])

    async def test_unproven_completion_is_not_reclassified_as_input_or_blocked(self):
        for summary in ("已经打开计算器了", "完成", "已打开计算器，你是要科学还是普通模式？"):
            with self.subTest(summary=summary):
                session = Mock()
                session.run_codex_turn = AsyncMock(return_value=json.dumps({
                    "outcome": "completed", "summary": summary, "evidence_steps": []}))
                session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
                    "outcome": "needs_input", "summary": summary, "evidence_steps": []}))
                runtime = CodexAgentRuntime(session, model="fixture")
                with self.assertRaises(TaskFailure) as failure:
                    await runtime.execute(RuntimeRequest("打开计算器", "policy", RESULT_SCHEMA), [], AsyncMock(),
                                          self.executor.validate_result, lambda _: None)
                self.assertEqual(failure.exception.code, "missing_evidence")
                session.run_isolated_structured_turn.assert_not_awaited()

    async def test_clarification_repair_cannot_invent_question_evidence_or_success(self):
        summary = "你是要打开哪个计算器？"
        for changes in ({"outcome": "completed"}, {"outcome": "blocked"},
                        {"summary": "请先登录"}, {"evidence_steps": [1]}):
            with self.subTest(changes=changes):
                session = Mock()
                session.run_codex_turn = AsyncMock(return_value=json.dumps({
                    "outcome": "completed", "summary": summary, "evidence_steps": []}))
                session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
                    "outcome": "needs_input", "summary": summary, "evidence_steps": [], **changes}))
                with self.assertRaises(TaskFailure) as failure:
                    await CodexAgentRuntime(session, model="fixture").execute(
                        RuntimeRequest("打开计算器", "policy", RESULT_SCHEMA), [], AsyncMock(),
                        self.executor.validate_result, lambda _: None)
                self.assertEqual(failure.exception.code, "missing_evidence")

    def test_needs_input_after_actions_requires_actual_evidence(self):
        self.executor.observations[1] = {"step": 1, "success": True}
        result = {"outcome": "needs_input", "summary": "要选哪首歌？", "evidence_steps": []}
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result(json.dumps(result))
        self.assertEqual(failure.exception.code, "missing_result_evidence")
        result["evidence_steps"] = [1]
        self.assertEqual(self.executor.validate_result(json.dumps(result))["outcome"], "needs_input")

    async def test_native_music_events_are_recorded_before_final_validation(self):
        case = json.loads((Path(__file__).parent / "fixtures/native_music_result.json").read_text())
        server = CodexAppServer(
            output=self.executor.output, on_tool_call=self.executor.call_tool,
            on_tool_observed=self.executor.observe_native_tool,
            approve=AsyncMock(), current_app=lambda: "", report=lambda *_: None,
            log=lambda *_args, **_kwargs: None)
        server.turn_id = "current-turn"
        server.completed = asyncio.get_running_loop().create_future()
        server.tools = self.executor.bind_tools([
            {"name": row["tool"], "inputSchema": {"type": "object"}} for row in case["calls"]])
        reader = asyncio.StreamReader()
        for row in case["calls"]:
            started = {"type": "mcpToolCall", "server": "boxagent_cua", "status": "inProgress",
                       "id": row["id"], "tool": row["tool"], "arguments": row["arguments"]}
            completed = {**started, "status": "completed", "result": {
                "content": [{"type": "text", "text": row["text"]}]}}
            for method, item in (("item/started", started), ("item/completed", completed),
                                 ("item/completed", completed)):
                reader.feed_data((json.dumps({"method": method, "params": {
                    "turnId": "current-turn", "item": item}}) + "\n").encode())
        reader.feed_data((json.dumps({"method": "item/completed", "params": {
            "turnId": "current-turn", "item": {"type": "agentMessage", "id": "final",
                                                  "text": json.dumps(case["reported_result"])}}}) + "\n").encode())
        reader.feed_data((json.dumps({"method": "turn/completed", "params": {
            "turn": {"id": "current-turn", "status": "completed"}}}) + "\n").encode())
        reader.feed_eof()
        server.process = Mock(stdout=reader)
        await server.read()
        self.assertEqual(server.completed.result()["status"], "completed")
        result = self.executor.validate_result(server.agent_text)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["steps"], 2)
        self.assertEqual([item["source"] for item in result["evidence"]], ["native_mcp", "native_mcp"])
        self.assertEqual(self.executor.session.calls, [])  # Recording must never repeat the actions.
        self.assertIn("自动过渡", (self.executor.output / "step-1.txt").read_text())
        invalid = {**case["reported_result"], "evidence_steps": [99]}
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result(json.dumps(invalid))
        self.assertEqual(failure.exception.code, "invalid_evidence")

    async def test_native_tool_history_and_failed_results_do_not_prove_completion(self):
        server = CodexAppServer(
            output=self.executor.output, on_tool_call=self.executor.call_tool,
            on_tool_observed=self.executor.observe_native_tool,
            approve=AsyncMock(), current_app=lambda: "", report=lambda *_: None,
            log=lambda *_args, **_kwargs: None)
        server.turn_id = "current"
        server.tools = self.executor.bind_tools([{"name": "get_app_state"}])
        item = {"id": "call", "type": "mcpToolCall", "tool": "get_app_state",
                "server": "boxagent_cua", "arguments": {"app": "Music"}, "status": "inProgress"}
        await server.observe_native_tool_event("item/started", {"turnId": "old", "item": item})
        await server.observe_native_tool_event("item/completed", {"turnId": "current", "item": {
            **item, "status": "completed", "result": {"content": [{"type": "text", "text": "old result"}]}}})
        self.assertEqual(self.executor.observations, {})
        await server.observe_native_tool_event("item/started", {"turnId": "current", "item": item})
        await server.observe_native_tool_event("item/completed", {"turnId": "current", "item": {
            **item, "status": "failed", "error": {"message": "noWindowsAvailable"}}})
        self.assertFalse(self.executor.observations[1]["success"])
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result(json.dumps({"outcome": "completed", "summary": "已打开",
                                                       "evidence_steps": [1]}))
        self.assertEqual(failure.exception.code, "failed_evidence")
        server.bind_task(on_tool_call=self.executor.call_tool, on_tool_observed=self.executor.observe_native_tool,
                         approve=AsyncMock(), current_app=lambda: "", report=lambda *_: None,
                         log=lambda *_args, **_kwargs: None)
        self.assertEqual(server.native_tool_calls, {})
        self.assertEqual(server.observed_native_calls, set())

    async def test_plain_clarification_is_reformatted_without_replaying_actions(self):
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value="Z V 是指哪个模型？请说清楚。")
        session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
            "outcome": "needs_input", "summary": "Z V 是指哪个模型？请说清楚。", "evidence_steps": []}))
        runtime = CodexAgentRuntime(session, model="fixture")
        request = RuntimeRequest("如果用 Z V 呢", "policy", RESULT_SCHEMA)
        call_tool = AsyncMock()
        text = await runtime.execute(request, [], call_tool,
                                     lambda value: validate_task_result(value, {}, []), lambda _: None)
        self.assertEqual(json.loads(text)["outcome"], "needs_input")
        session.run_codex_turn.assert_awaited_once()
        session.run_isolated_structured_turn.assert_awaited_once()
        call_tool.assert_not_awaited()

    async def test_result_reformat_cannot_turn_unproven_prose_into_success(self):
        session = Mock()
        session.run_codex_turn = AsyncMock(return_value="已经完成")
        session.run_isolated_structured_turn = AsyncMock(return_value=json.dumps({
            "outcome": "completed", "summary": "已经完成", "evidence_steps": []}))
        runtime = CodexAgentRuntime(session, model="fixture")
        with self.assertRaises(TaskFailure) as failure:
            await runtime.execute(RuntimeRequest("打开计算器", "policy", RESULT_SCHEMA), [], AsyncMock(),
                                  lambda value: validate_task_result(value, {}, []), lambda _: None)
        self.assertEqual(failure.exception.code, "missing_evidence")

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.executor = TaskExecutor(
            "test", provider="test", model="test-model",
            output=Path(self.directory.name),
            runtime_factory=lambda _session: AsyncMock(),
            session_factory=FakeSession,
            tool_gateway_factory=ComputerUseToolGateway,
            request_factory=RuntimeRequestBuilder().prepare,
        )
        self.executor.progress = lambda *_args: None
        self.executor.goal = "测试目标"
        self.executor.session = FakeSession(
            output=self.executor.output, on_tool_call=self.executor.call_tool,
            approve=AsyncMock(), current_app=lambda: self.executor.active_app,
            report=self.executor.report, log=self.executor.log,
        )
        self.executor.gateway = ComputerUseToolGateway(
            self.executor.session, output=self.executor.output, goal=self.executor.goal,
            report=self.executor.report, log=self.executor.log,
            is_stopped=lambda: self.executor.stopped)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_discovered_tool_for_unseen_app_is_forwarded_unchanged(self):
        spec = {"name": "new_operation", "description": "运行时新增工具", "inputSchema": {
            "type": "object", "properties": {"app": {"type": "string"}, "text": {"type": "string"}},
            "required": ["app", "text"], "additionalProperties": False}}
        bindings = self.executor.bind_tools([spec])
        arguments = {"app": "org.example.unseen", "text": "文字输入，不是算式"}
        result = await self.executor.call_tool(bindings[0]["name"], arguments)
        self.assertTrue(result["success"])
        self.assertEqual(self.executor.session.calls, [("new_operation", arguments)])

    def test_tool_schema_order_is_stable_for_prompt_caching(self):
        bindings = self.executor.bind_tools([
            {"name": "z_action", "inputSchema": {"type": "object"}},
            {"name": "a_action", "inputSchema": {"type": "object"}},
        ])
        self.assertEqual([item["name"] for item in bindings],
                         ["desktop_a_action", "desktop_z_action"])

    async def test_cancel_prevents_any_new_dispatch(self):
        self.executor.stopped = True
        result = await self.executor.call_tool("desktop_click", {"app": "any"})
        self.assertFalse(result["success"])
        self.assertEqual(self.executor.session.calls, [])

    def test_screenshot_is_delivered_to_model(self):
        result = model_content({"content": [
            {"type": "image", "mimeType": "image/png", "data": "AAAA"}]}, 2)
        self.assertEqual(result[-1], {
            "type": "inputImage", "imageUrl": "data:image/png;base64,AAAA"})

    async def test_tool_response_preserves_original_goal_after_observation(self):
        self.executor.goal = "帮我使用 vscode 新建一个测试文件"
        self.executor.gateway.goal = self.executor.goal
        self.executor.bind_tools([{"name": "get_app_state", "inputSchema": {"type": "object"}}])
        result = await self.executor.call_tool("desktop_get_app_state", {"app": "Code"})
        self.assertIn(self.executor.goal, result["contentItems"][-1]["text"])
        self.assertIn("当前观察步骤为 1", result["contentItems"][-1]["text"])
        self.assertEqual(result["contentItems"][1]["text"], "当前界面")

    def test_unsupported_blocked_report_after_actions_is_not_shown(self):
        self.executor.observations[17] = {"step": 17, "success": True}
        text = json.dumps({"outcome": "blocked", "summary":
            "未收到具体操作目标，请告诉我需要在桌面应用中完成什么任务。", "evidence_steps": []})
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result(text)
        self.assertEqual(failure.exception.code, "missing_result_evidence")
        self.assertNotIn("未收到具体操作目标", str(failure.exception))
        self.assertIn(text, failure.exception.detail)

    def test_blocked_with_observed_obstacle_is_preserved(self):
        self.executor.observations[1] = {"step": 1, "success": True}
        text = json.dumps({"outcome": "blocked", "summary": "需要登录", "evidence_steps": [1]})
        self.assertEqual(self.executor.validate_result(text)["summary"], "需要登录")

    def test_final_json_after_provider_prose_is_accepted(self):
        self.executor.observations[1] = {"step": 1, "success": True}
        text = ("计算器已显示结果。\n\n" + json.dumps({
            "outcome": "completed", "summary": "结果为 42", "evidence_steps": [1]}))
        result = self.executor.validate_result(text)
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual(result["summary"], "结果为 42")

    def test_provider_prose_without_terminal_json_is_rejected(self):
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result("计算器已经完成，但没有结构化结果。")
        self.assertEqual(failure.exception.code, "invalid_result")

    def test_completion_without_actual_evidence_is_rejected(self):
        text = json.dumps({"outcome": "completed", "summary": "播放了", "evidence_steps": [10]})
        with self.assertRaises(TaskFailure) as failure:
            self.executor.validate_result(text)
        self.assertIn("不存在的观察", failure.exception.detail)

    async def test_failed_tool_observation_preserves_real_failure(self):
        self.executor.bind_tools([{"name": "get_app_state", "inputSchema": {"type": "object"}}])
        self.executor.session.response = {"isError": True, "content": [
            {"type": "text", "text": "bootstrapTimedOut"}]}
        await self.executor.call_tool("desktop_get_app_state", {"app": "Code"})
        text = json.dumps({"outcome": "blocked", "summary": "连接失败", "evidence_steps": [1]})
        result = self.executor.validate_result(text)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("电脑操作服务连接超时", result["summary"])
        self.assertFalse(result["evidence"][0]["success"])
        self.assertTrue((self.executor.output / "step-1.txt").exists())

    def test_failed_observation_cannot_prove_success(self):
        self.executor.observations[1] = {"step": 1, "success": False}
        text = json.dumps({"outcome": "completed", "summary": "完成", "evidence_steps": [1]})
        with self.assertRaises(TaskFailure):
            self.executor.validate_result(text)

    async def test_exception_still_writes_terminal_result_and_traceback(self):
        self.executor._execute = AsyncMock(side_effect=RuntimeError("测试异常"))
        with self.assertRaises(RuntimeError):
            await self.executor.run("测试目标", lambda *_: None, None)
        result = json.loads((self.executor.output / "result.json").read_text())
        status = json.loads((self.executor.output / "status.json").read_text())
        self.assertEqual(result["outcome"], "failed")
        self.assertFalse(result["runtime_process_alive"])
        self.assertFalse(status["running"])
        self.assertIn("traceback", (self.executor.output / "events.jsonl").read_text())

    async def test_executor_captures_environment_for_each_runtime_request(self):
        captured = {}
        environment = EnvironmentContext(
            captured_at="2026-10-06T15:30:00+08:00",
            timezone="Asia/Shanghai", weekday="星期二")
        provider = Mock()
        provider.capture.return_value = environment

        class Runtime:
            async def execute(self, request, *_args):
                captured["request"] = request
                return "ignored"

        executor = TaskExecutor(
            "environment", provider="test", model="test-model",
            output=Path(self.directory.name) / "environment",
            runtime_factory=lambda _session: Runtime(),
            session_factory=FakeSession,
            tool_gateway_factory=ComputerUseToolGateway,
            request_factory=RuntimeRequestBuilder().prepare,
            environment_provider=provider,
        )
        executor.validate_result = lambda _text: {
            "outcome": "blocked", "summary": "测试结束"}
        await executor._execute("现在几点")

        provider.capture.assert_called_once_with()
        self.assertEqual(captured["request"].query, "现在几点")
        self.assertIn("Asia/Shanghai", captured["request"].evidence_context)

    async def test_executor_injects_relevant_memory_as_fenced_evidence(self):
        captured = {}

        class Runtime:
            async def execute(self, request, *_args):
                captured["request"] = request
                return "ignored"

        memory = Mock(evidence=AsyncMock(return_value=[{
            "memory_id": "mem_1", "kind": "preference",
            "content": "用户喜欢爵士乐", "source": "jev_mem"}]))

        def session_factory(**kwargs):
            kwargs.pop("product_session_id", None)
            kwargs.pop("runtime_thread_id", None)
            kwargs.pop("on_thread_bound", None)
            return FakeSession(**kwargs)

        executor = TaskExecutor(
            "memory", provider="test", model="test-model",
            output=Path(self.directory.name) / "memory",
            runtime_factory=lambda _session: Runtime(),
            session_factory=session_factory,
            tool_gateway_factory=ComputerUseToolGateway,
            request_factory=RuntimeRequestBuilder().prepare,
            session_id="ses_1", memory=memory,
        )
        executor.validate_result = lambda _text: {
            "outcome": "blocked", "summary": "测试结束"}

        await executor._execute("播放点音乐")

        memory.evidence.assert_awaited_once_with(
            "播放点音乐", session_id="ses_1", top_k=5)
        self.assertIn("用户喜欢爵士乐",
                      captured["request"].evidence_context)

    async def test_executor_persists_auditable_context_request_and_memory_trace(self):
        captured = {}

        class Runtime:
            async def execute(self, request, *_args):
                captured["request"] = request
                return "ignored"

        memory = Mock()
        memory.evidence_with_trace = AsyncMock(return_value={
            "items": [{"id": "mem-1", "kind": "preference",
                       "content": "用户喜欢乌龙茶"}],
            "retrieval": {"query": "推荐饮料", "mode": "direct", "top_k": 5,
                          "elapsed_ms": 12.5, "profile": [],
                          "evidence": [{"id": "mem-1"}], "narrative": [],
                          "trace": {"degraded": False}},
        })

        def session_factory(**kwargs):
            kwargs.pop("product_session_id", None)
            kwargs.pop("runtime_thread_id", None)
            kwargs.pop("on_thread_bound", None)
            return FakeSession(**kwargs)

        output = Path(self.directory.name) / "audit"
        executor = TaskExecutor(
            "audit", provider="deepseek", model="deepseek-flash", output=output,
            runtime_factory=lambda _session: Runtime(),
            session_factory=session_factory,
            tool_gateway_factory=ComputerUseToolGateway,
            request_factory=RuntimeRequestBuilder().prepare,
            session_id="ses-audit", interaction_id="int-audit", memory=memory)
        executor.validate_result = lambda _text: {
            "outcome": "blocked", "summary": "测试结束"}

        await executor._execute("推荐饮料")

        context = json.loads((output / "context.json").read_text())
        request = json.loads((output / "request.json").read_text())
        retrieval = json.loads((output / "memory-retrieval.json").read_text())
        self.assertEqual(context["memory_evidence"][0]["id"], "mem-1")
        self.assertIn("用户喜欢乌龙茶", request["evidence_context"])
        self.assertEqual(retrieval["injected_items"][0]["content"], "用户喜欢乌龙茶")
        self.assertGreaterEqual(retrieval["elapsed_ms"], 0)

    async def test_environment_capture_failure_degrades_to_empty_evidence(self):
        provider = Mock()
        provider.capture.side_effect = RuntimeError("clock unavailable")
        self.executor.environment_provider = provider

        self.assertIsNone(self.executor.capture_environment())
        self.assertIn("environment_capture_failed",
                      (self.executor.output / "events.jsonl").read_text())

    async def test_cancel_still_writes_terminal_result(self):
        self.executor._execute = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.executor.run("测试目标", lambda *_: None, None)
        result = json.loads((self.executor.output / "result.json").read_text())
        self.assertEqual(result["outcome"], "cancelled")

    def test_sensitive_fields_and_inline_images_are_redacted(self):
        data = redact({"api_key": "private", "text": "Bearer private data:image/png;base64,AAAA"})
        self.assertNotIn("private", json.dumps(data))
        self.assertNotIn("base64", data["text"])


class CodexSessionTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CodexAppServer(
            output=Path(directory.name), on_tool_call=AsyncMock(), approve=AsyncMock(),
            current_app=lambda: "", report=lambda *_: None,
            log=lambda *_args, **_kwargs: None)

    def test_deepseek_profile_uses_responses_without_leaking_key_to_arguments(self):
        catalog = Path(__file__).resolve().parents[1] / "assets/model-catalogs/deepseek.json"
        profile = ModelProfile(
            provider="deepseek", model="deepseek-flash", display_name="DeepSeek",
            base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY",
            api_key="secret-for-test", model_catalog=catalog,
        )
        session = self.make_session()
        session.model_profile = profile

        arguments = session.launch_arguments(Path("/codex"), Path("/computer-use"))
        command = " ".join(arguments)
        self.assertIn('model_provider="deepseek"', command)
        self.assertIn('model="deepseek-flash"', command)
        self.assertIn('model_providers.deepseek.wire_api="responses"', command)
        self.assertIn('model_providers.deepseek.env_key="DEEPSEEK_API_KEY"', command)
        self.assertIn("model_catalog_json=", command)
        self.assertNotIn("secret-for-test", command)
        self.assertEqual(session.launch_environment()["DEEPSEEK_API_KEY"], "secret-for-test")

    def test_app_server_uses_native_computer_use_turn_notifier(self):
        session = self.make_session()
        session.computer_use_home = Path("/native-installation-home")

        arguments = session.launch_arguments(Path("/codex"), Path("/computer-use"))

        notify = next(value for value in arguments if value.startswith("notify="))
        self.assertEqual(json.loads(notify.removeprefix("notify=")), [
            "/usr/bin/env", "CODEX_HOME=/native-installation-home", "/computer-use", "turn-ended"])
        self.assertNotIn("notify=[]", arguments)

    async def test_matching_runtime_request_reuses_one_codex_thread(self):
        session = self.make_session()
        session.tools = [{"name": "click"}]
        session.rpc = AsyncMock(return_value={"thread": {"id": "thread-1"}})

        first = await session.ensure_thread(model="model", instructions="stable")
        second = await session.ensure_thread(model="model", instructions="stable")

        self.assertEqual((first, second),
                         (("thread-1", "started"), ("thread-1", "reused")))
        session.rpc.assert_awaited_once()
        self.assertFalse(session.rpc.await_args.args[1]["ephemeral"])

    async def test_checkpoint_turn_uses_ephemeral_thread_without_dynamic_tools(self):
        session = self.make_session()
        calls = []

        async def rpc(method, params, timeout=120):
            del timeout
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "checkpoint-thread"}}
            if method == "turn/start":
                def complete():
                    session.agent_text = '{"summary":"ok"}'
                    session.completed.set_result({"status": "completed"})
                asyncio.get_running_loop().call_soon(complete)
                return {"turn": {"id": "checkpoint-turn"}}
            self.fail("unexpected RPC: " + method)

        session.rpc = rpc
        result = await session.run_isolated_structured_turn(
            model="deepseek-flash", instructions="summarize", query="{}",
            output_schema={"type": "object"})

        self.assertEqual(result, '{"summary":"ok"}')
        self.assertTrue(calls[0][1]["ephemeral"])
        self.assertEqual(calls[0][1]["dynamicTools"], [])
        self.assertEqual(calls[0][1]["approvalPolicy"], "never")
        self.assertIsNone(session.turn_id)

    async def test_instruction_change_starts_a_new_runtime_epoch(self):
        session = self.make_session()
        session.tools = [{"name": "click"}]
        session.rpc = AsyncMock(side_effect=[
            {"thread": {"id": "thread-1"}}, {"thread": {"id": "thread-2"}},
        ])

        await session.ensure_thread(model="model", instructions="persona-a")
        await session.ensure_thread(model="model", instructions="persona-b")

        self.assertEqual(session.thread_id, "thread-2")
        self.assertEqual(session.rpc.await_count, 2)

    async def test_skill_change_starts_new_epoch_instead_of_resuming_bound_thread(self):
        session = self.make_session()
        session.tools = [{"name": "click"}]
        session.thread_id = "thread-old"
        session.thread_signature = (
            "model", "stable", json.dumps(session.tools, sort_keys=True, ensure_ascii=False),
            ("old-skill",))

        class FakeSkillAdapter:
            signature = ("new-skill",)

            async def sync(self, _rpc, *, force=False):
                del force
                return []

        session.skill_adapter = FakeSkillAdapter()
        session.rpc = AsyncMock(return_value={"thread": {"id": "thread-new"}})

        result = await session.ensure_thread(
            model="model", instructions="stable",
            preferred_thread_id="thread-old", product_session_id="ses_1")

        self.assertEqual(result, ("thread-new", "started"))
        self.assertEqual(session.rpc.await_args.args[0], "thread/start")

    async def test_bound_thread_is_resumed_instead_of_replaying_history(self):
        session = self.make_session()
        session.tools = [{"name": "click"}]
        session.rpc = AsyncMock(return_value={"thread": {"id": "thread-old"}})

        result = await session.ensure_thread(
            model="model", instructions="stable",
            preferred_thread_id="thread-old", product_session_id="ses_1")

        self.assertEqual(result, ("thread-old", "resumed"))
        self.assertEqual(session.rpc.await_args.args[0], "thread/resume")
        self.assertEqual(session.rpc.await_args.args[1]["threadId"], "thread-old")

    async def test_warm_thread_injects_only_cross_runtime_history(self):
        session = self.make_session()
        session.ensure_thread = AsyncMock(return_value=("thread-1", "reused"))
        calls = []

        async def rpc(method, params, timeout=120):
            del timeout
            calls.append((method, params))
            if method == "thread/inject_items":
                return {}
            if method == "turn/start":
                asyncio.get_running_loop().call_soon(
                    session.completed.set_result, {"status": "completed"})
                return {"turn": {"id": "turn-1"}}
            self.fail("unexpected RPC: " + method)

        session.rpc = rpc
        session.agent_text = "{}"
        await session.run_codex_turn(
            model="model", instructions="stable", query="打开音乐",
            output_schema={},
            history=(RuntimeMessage("user", "完整旧历史", sequence=1),),
            history_delta=(
                RuntimeMessage("user", "Qwen 新增问题", sequence=4),
                RuntimeMessage("assistant", "Qwen 新增回答", sequence=5),
            ))

        self.assertEqual([method for method, _params in calls],
                         ["thread/inject_items", "turn/start"])
        injected = calls[0][1]["items"]
        self.assertEqual(injected, [
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Qwen 新增问题"}]},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "Qwen 新增回答"}]},
        ])
        self.assertEqual(calls[1][1]["input"][0]["text"], "打开音乐")

    async def test_new_thread_injects_full_history_before_current_goal(self):
        session = self.make_session()
        session.ensure_thread = AsyncMock(return_value=("thread-1", "started"))
        calls = []

        async def rpc(method, params, timeout=120):
            del timeout
            calls.append((method, params))
            if method == "thread/inject_items":
                return {}
            asyncio.get_running_loop().call_soon(
                session.completed.set_result, {"status": "completed"})
            return {"turn": {"id": "turn-1"}}

        session.rpc = rpc
        session.agent_text = "{}"
        await session.run_codex_turn(
            model="model", instructions="stable", query="打开音乐",
            output_schema={},
            history=(RuntimeMessage("assistant", "之前聊过音乐", sequence=2),),
            history_delta=(RuntimeMessage("user", "不应选择我", sequence=5),))

        self.assertEqual(calls[0][0], "thread/inject_items")
        self.assertEqual(calls[0][1]["items"][0]["content"][0]["text"],
                         "之前聊过音乐")
        self.assertEqual(calls[1][0], "turn/start")
        self.assertEqual(calls[1][1]["input"][0]["text"], "打开音乐")

    async def test_non_conversation_evidence_remains_in_current_turn(self):
        session = self.make_session()
        session.ensure_thread = AsyncMock(return_value=("thread-1", "reused"))

        async def rpc(method, params, timeout=120):
            del timeout
            self.assertEqual(method, "turn/start")
            session.captured_turn = params
            asyncio.get_running_loop().call_soon(
                session.completed.set_result, {"status": "completed"})
            return {"turn": {"id": "turn-1"}}

        session.rpc = rpc
        session.agent_text = "{}"
        await session.run_codex_turn(
            model="model", instructions="stable", query="打开音乐",
            output_schema={}, evidence_context="[记忆证据]喜欢轻音乐")

        self.assertEqual(session.captured_turn["input"][0]["text"],
                         "[记忆证据]喜欢轻音乐\n当前用户请求：\n打开音乐")

    async def test_product_task_reverts_reference_turn_after_completion(self):
        session = self.make_session()
        session.ensure_thread = AsyncMock(return_value=("thread-1", "reused"))
        calls = []
        async def rpc(method, params, timeout=120):
            calls.append((method, params))
            if method == "turn/start":
                asyncio.get_running_loop().call_soon(session.completed.set_result, {"status": "completed"})
                return {"turn": {"id": "turn-reference"}}
            if method == "thread/revert":
                return {}
            self.fail(method)
        session.rpc = rpc
        await session.run_codex_turn(model="m", instructions="stable", query="q", output_schema={},
                                     evidence_context="temporary", product_session_id="ses_test")
        self.assertEqual(calls[-1], ("thread/revert", {"threadId": "thread-1", "beforeTurnId": "turn-reference"}))

    async def test_reference_revert_failure_invalidates_thread_binding(self):
        session = self.make_session()
        session.ensure_thread = AsyncMock(return_value=("thread-1", "reused"))
        async def rpc(method, params, timeout=120):
            if method == "turn/start":
                asyncio.get_running_loop().call_soon(session.completed.set_result, {"status": "completed"})
                return {"turn": {"id": "turn-reference"}}
            raise RuntimeError("revert failed")
        session.rpc = rpc
        bound = AsyncMock()
        await session.run_codex_turn(model="m", instructions="stable", query="q", output_schema={},
            evidence_context="temporary", product_session_id="ses_test", on_thread_bound=bound)
        self.assertIsNone(session.thread_id)
        self.assertIsNone(session.thread_signature)
        self.assertEqual(bound.await_args_list[-1].args, ("",))

    async def test_sync_callback_failure_still_removes_reference(self):
        session = self.make_session()
        session.ensure_thread = AsyncMock(return_value=("thread-1", "reused"))
        calls = []
        async def rpc(method, params, timeout=120):
            calls.append(method)
            return {"turn": {"id": "turn-reference"}} if method == "turn/start" else {}
        session.rpc = rpc
        with self.assertRaises(RuntimeError):
            await session.run_codex_turn(model="m", instructions="stable", query="q", output_schema={},
                evidence_context="temporary", product_session_id="ses_test",
                on_context_synced=AsyncMock(side_effect=RuntimeError("store unavailable")))
        self.assertEqual(calls, ["turn/start", "thread/revert"])

    async def test_new_product_session_does_not_reuse_another_sessions_thread(self):
        session = self.make_session()
        session.tools = [{"name": "click"}]
        session.thread_id = "thread-session-a"
        session.thread_signature = (
            "model", "stable", json.dumps(session.tools, sort_keys=True, ensure_ascii=False))
        session.rpc = AsyncMock(return_value={"thread": {"id": "thread-session-b"}})

        result = await session.ensure_thread(
            model="model", instructions="stable",
            product_session_id="ses_b")

        self.assertEqual(result, ("thread-session-b", "started"))
        self.assertEqual(session.rpc.await_args.args[0], "thread/start")

    def test_dedicated_codex_home_is_passed_only_to_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            session = self.make_session()
            session.codex_home = Path(directory) / "codex-home"
            environment = session.launch_environment()
            expected = str(Path(directory) / "codex-home")
        self.assertEqual(environment["CODEX_HOME"], expected)

    def test_openai_profile_does_not_receive_deepseek_configuration_or_key(self):
        session = self.make_session()
        session.model_profile = ModelProfile(
            provider="openai", model="gpt-test", display_name="OpenAI")
        arguments = session.launch_arguments(Path("/codex"), Path("/computer-use"))
        command = " ".join(arguments)
        self.assertIn('model_provider="openai"', command)
        self.assertNotIn("deepseek", command.lower())
        self.assertNotIn("DEEPSEEK_API_KEY", session.launch_environment())

    def test_deepseek_profile_fails_before_spawn_when_key_is_missing(self):
        session = self.make_session()
        session.model_profile = ModelProfile(
            provider="deepseek", model="deepseek-flash", display_name="DeepSeek",
            base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY",
            model_catalog=Path(__file__).resolve().parents[1]
            / "assets/model-catalogs/deepseek.json",
        )
        with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
            session.launch_arguments(Path("/codex"), Path("/computer-use"))

    async def test_cleanup_notifies_only_own_turn_and_waits(self):
        session = self.make_session()
        session.codex_home = Path("/private-session-home")
        session.computer_use_home = Path("/native-installation-home")
        session.thread_id = "test-thread"
        session.turn_id = "own-turn"
        session.turn_input_messages = ["打开知乎看看 AI 文章"]
        session.agent_text = "已经完成浏览。"
        session.client = Path("/client")
        session.rpc = AsyncMock(return_value={"exitCode": 0, "stdout": "", "stderr": ""})
        with patch("boxagent.infrastructure.runtimes.codex.app_server.asyncio.create_subprocess_exec",
                   AsyncMock()) as spawn:
            await session.end_turn()
        spawn.assert_not_awaited()
        self.assertEqual(session.rpc.await_args.args[0], "command/exec")
        params = session.rpc.await_args.args[1]
        args = params["command"]
        self.assertEqual(args[:2], ["/client", "turn-ended"])
        self.assertEqual(json.loads(args[2])["thread-id"], "test-thread")
        self.assertEqual(json.loads(args[2])["turn-id"], "own-turn")
        self.assertEqual(json.loads(args[2])["client"], "boxagent-pet")
        self.assertEqual(json.loads(args[2])["input-messages"],
                         ["打开知乎看看 AI 文章"])
        self.assertEqual(json.loads(args[2])["last-assistant-message"],
                         "已经完成浏览。")
        self.assertEqual(params["timeoutMs"], 8000)
        self.assertEqual(params["env"], {"CODEX_HOME": "/native-installation-home"})
        self.assertEqual(session.rpc.await_args.kwargs["timeout"], 10)
        self.assertEqual(session.cleanup_status, "notified")
        self.assertEqual(session.turn_input_messages, [])

    async def test_computer_use_metadata_tracks_completed_and_interrupted_turns(self):
        session = self.make_session()
        session.thread_id = "shared-thread"
        session.client = Path("/client")
        session.process = Mock(returncode=None)
        session.ensure_thread = AsyncMock(return_value=("shared-thread", "reused"))
        calls = []
        notifications = []
        turn_number = 0

        async def rpc(method, params, timeout=120):
            nonlocal turn_number
            if method == "turn/start":
                turn_number += 1
                return {"turn": {"id": f"turn-{turn_number}"}}
            calls.append((method, params))
            if method == "turn/interrupt":
                session.completed.set_result({"status": "interrupted"})
            if method == "command/exec":
                notifications.append(json.loads(params["command"][2]))
                return {"exitCode": 0}
            return {"content": []}

        session.rpc = rpc
        with patch("boxagent.infrastructure.runtimes.codex.app_server.asyncio.create_subprocess_exec",
                   AsyncMock()) as spawn:
            for number in (1, 2):
                task = asyncio.create_task(session.run_codex_turn(
                    model="test-model", instructions="stable", query="读取计算器",
                    output_schema={}))
                await asyncio.sleep(0)
                await session.call_tool("get_app_state", {"app": "com.apple.calculator"})
                await session.call_tool("get_app_state", {"app": "com.apple.calculator"})
                metadata = calls[-1][1]["_meta"]
                self.assertEqual(metadata["threadId"], "shared-thread")
                context = metadata["x-codex-turn-metadata"]
                self.assertEqual(context["session_id"], "shared-thread")
                self.assertEqual(context["thread_id"], "shared-thread")
                self.assertEqual(context["turn_id"], f"turn-{number}")
                self.assertEqual(context["model"], "test-model")
                self.assertGreater(context["turn_started_at_unix_ms"], 0)
                self.assertEqual(metadata, calls[-2][1]["_meta"])
                if number == 1:
                    session.completed.set_result({"status": "completed"})
                    await task
                else:
                    await session.interrupt()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                await session.release_task()
                self.assertEqual(notifications[-1]["thread-id"], context["thread_id"])
                self.assertEqual(notifications[-1]["turn-id"], context["turn_id"])
                self.assertIsNone(session.turn_metadata)
                self.assertIsNone(session.turn_id)
            spawn.assert_not_awaited()

        self.assertNotEqual(notifications[0]["turn-id"], notifications[1]["turn-id"])
        await session.call_tool("get_app_state", {"app": "com.apple.calculator"})
        self.assertNotIn("x-codex-turn-metadata", calls[-1][1]["_meta"])

    async def test_cleanup_timeout_is_logged_without_hiding_task_result(self):
        session = self.make_session()
        session.thread_id = "test-thread"
        session.turn_id = "own-turn"
        session.client = Path("/client")
        session.rpc = AsyncMock(side_effect=TimeoutError)
        await session.end_turn()
        self.assertEqual(session.cleanup_status, "failed")
        self.assertIsNone(session.turn_id)

    async def test_cleanup_reports_disconnected_runtime_without_direct_fallback(self):
        session = self.make_session()
        session.thread_id = "test-thread"
        session.turn_id = "own-turn"
        session.client = Path("/client")
        with patch("boxagent.infrastructure.runtimes.codex.app_server.asyncio.create_subprocess_exec",
                   AsyncMock()) as spawn:
            await session.end_turn()
        spawn.assert_not_awaited()
        self.assertEqual(session.cleanup_status, "failed")
        self.assertIsNone(session.turn_id)

    async def test_cleanup_without_turn_does_not_send_global_notification(self):
        session = self.make_session()
        session.rpc = AsyncMock()
        await session.end_turn()
        session.rpc.assert_not_awaited()

    async def test_computer_use_approval_is_automatic_by_default(self):
        session = self.make_session()
        session.send = AsyncMock()
        await session.callback({"id": 1, "method": "mcpServer/elicitation/request",
            "params": {"serverName": "boxagent_cua", "requestedSchema": {"properties": {}}}})
        session.approve.assert_not_awaited()
        session.send.assert_awaited_once_with({
            "id": 1, "result": {"action": "accept", "content": {}}})

    async def test_manual_approval_can_be_enabled(self):
        session = self.make_session()
        session.auto_approve = False
        session.send = AsyncMock()
        session.approve = AsyncMock(return_value=False)
        await session.callback({"id": 1, "method": "mcpServer/elicitation/request",
            "params": {"serverName": "boxagent_cua", "requestedSchema": {"properties": {}}}})
        session.approve.assert_awaited_once()
        session.send.assert_awaited_once_with({"id": 1, "result": {"action": "decline"}})

    async def test_runtime_delegates_permission_policy_to_harness(self):
        session = self.make_session()
        session.send = AsyncMock()
        session.permission_check = Mock(return_value=False)
        params = {"serverName": "boxagent_cua", "requestedSchema": {"properties": {}}}
        await session.callback({"id": 1, "method": "mcpServer/elicitation/request",
                                "params": params})
        session.permission_check.assert_called_once_with(params, stopped=False)
        session.send.assert_awaited_once_with({"id": 1, "result": {"action": "decline"}})

    async def test_automatic_approval_rejects_data_requests_and_stopped_tasks(self):
        session = self.make_session()
        session.send = AsyncMock()
        cases = [
            ("other", {}, False),
            ("boxagent_cua", {"password": {"type": "string"}}, False),
            ("boxagent_cua", {}, True),
        ]
        for request_id, (server, properties, stopped) in enumerate(cases, 1):
            session.stopped = stopped
            await session.callback({"id": request_id,
                "method": "mcpServer/elicitation/request", "params": {
                    "serverName": server, "requestedSchema": {"properties": properties}}})
            session.send.assert_awaited_with({
                "id": request_id, "result": {"action": "decline"}})

    def test_runtime_resolver_requires_matching_host(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incomplete = root / "incomplete" / "codex"
            complete = root / "complete" / "codex"
            incomplete.parent.mkdir()
            complete.parent.mkdir()
            incomplete.write_text("")
            complete.write_text("")
            complete.with_name("codex-code-mode-host").write_text("")
            with patch.dict("os.environ", {"BOXAGENT_CODEX_BIN": str(incomplete),
                                             "CODEX_HOME": str(root)}), \
                    patch("boxagent.infrastructure.runtimes.codex.app_server.shutil.which",
                          return_value=str(complete)):
                self.assertEqual(resolve_codex_runtime(root), complete.resolve())


class CodexRuntimeHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_sequential_tasks_share_process_and_thread_owner(self):
        created = []

        class FakeServer:
            def __init__(self, **callbacks):
                created.append(self)
                self.callbacks = callbacks
                self.process = Mock(returncode=None, pid=42)
                self.tools = [{"name": "click"}]
                self.discovered_tools = [{"name": "click"}]
                self.thread_id = "thread-1"
                self.cleanup_status = "not_started"
                self.start_count = 0
                self.release_count = 0
                self.closed = False

            def bind_task(self, **callbacks):
                self.callbacks = callbacks

            async def start(self):
                self.start_count += 1
                return self.tools

            async def release_task(self):
                self.release_count += 1

            async def close(self):
                self.closed = True

        host = CodexRuntimeHost(
            output=Path("/tmp/runtime"), workspace=Path("/tmp"),
            model_profile=None, permission_check=Mock(), server_factory=FakeServer)
        callbacks = dict(on_tool_call=AsyncMock(), approve=AsyncMock(),
                         current_app=lambda: "", report=Mock(), log=Mock(),
                         auto_approve=True, output=Path("/tmp/task"))
        first = host.create_session(**callbacks)
        await first.start()
        process = first.process
        await first.close()
        second = host.create_session(**callbacks)
        await second.start()

        self.assertIs(second.process, process)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].thread_id, "thread-1")
        await second.close()
        await host.close()
        self.assertEqual(created[0].release_count, 2)
        self.assertTrue(created[0].closed)
