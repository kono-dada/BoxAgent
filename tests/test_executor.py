"""通用执行契约：应用、参数和证据不能依赖预设场景。"""

import json
import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from boxagent.executor import CodexExecutor, model_content
from boxagent.diagnostics import TaskFailure, redact


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.executor = CodexExecutor("test")
        self.executor.output = Path(self.directory.name)
        self.executor.thread_id = "test-thread"
        self.executor.turn_id = "test-turn"
        self.executor.progress = lambda *_args: None
        self.calls = []

        async def rpc(method, params):
            self.calls.append((method, params))
            return {"content": [{"type": "text", "text": "当前界面"}]}

        self.executor.rpc = rpc

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_discovered_tool_for_unseen_app_is_forwarded_unchanged(self):
        spec = {"name": "new_operation", "description": "运行时新增工具", "inputSchema": {
            "type": "object", "properties": {"app": {"type": "string"}, "text": {"type": "string"}},
            "required": ["app", "text"], "additionalProperties": False}}
        bindings = self.executor.bind_tools([spec])
        arguments = {"app": "org.example.unseen", "text": "文字输入，不是算式"}
        result = await self.executor.dynamic(bindings[0]["name"], arguments)
        self.assertTrue(result["success"])
        self.assertEqual(self.calls[0][1]["arguments"], arguments)
        self.assertEqual(self.calls[0][1]["tool"], "new_operation")
        self.assertEqual(self.calls[0][1]["_meta"], {"codex.session_id": "test-thread", "codex.turn_id": "test-turn"})

    async def test_cancel_prevents_any_new_dispatch(self):
        self.executor.stopped = True
        result = await self.executor.dynamic("desktop_click", {"app": "any"})
        self.assertFalse(result["success"])
        self.assertEqual(self.calls, [])

    async def test_cleanup_notifies_only_own_turn_and_waits(self):
        self.executor.turn_id = "own-turn"
        process = Mock(returncode=0, communicate=AsyncMock(return_value=(b"", b"")))
        with patch("boxagent.executor.asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
            await self.executor.end_computer_use_turn(Path("/client"))
        args = spawn.call_args.args
        self.assertEqual(args[:2], ("/client", "turn-ended"))
        self.assertEqual(json.loads(args[2])["thread-id"], "test-thread")
        self.assertEqual(json.loads(args[2])["turn-id"], "own-turn")
        process.communicate.assert_awaited_once()
        self.assertEqual(self.executor.cleanup_status, "notified")

    async def test_cleanup_timeout_is_logged_without_hiding_task_result(self):
        self.executor.turn_id = "own-turn"
        process = Mock(returncode=None, communicate=AsyncMock(side_effect=TimeoutError), wait=AsyncMock())
        with patch("boxagent.executor.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
            await self.executor.end_computer_use_turn(Path("/client"))
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()
        self.assertEqual(self.executor.cleanup_status, "failed")

    async def test_cleanup_without_turn_does_not_send_global_notification(self):
        self.executor.turn_id = None
        with patch("boxagent.executor.asyncio.create_subprocess_exec", AsyncMock()) as spawn:
            await self.executor.end_computer_use_turn(Path("/client"))
        spawn.assert_not_awaited()

    async def test_computer_use_approval_is_automatic_by_default(self):
        self.executor.send = AsyncMock()
        self.executor.approve = AsyncMock(return_value=True)
        await self.executor.callback({"id": 1, "method": "mcpServer/elicitation/request",
            "params": {"serverName": "boxagent_cua", "requestedSchema": {"properties": {}}}})
        self.executor.approve.assert_not_awaited()
        self.executor.send.assert_awaited_once_with({"id": 1, "result": {"action": "accept", "content": {}}})

    async def test_manual_approval_can_be_enabled(self):
        self.executor.auto_approve = False
        self.executor.send = AsyncMock()
        self.executor.approve = AsyncMock(return_value=False)
        await self.executor.callback({"id": 1, "method": "mcpServer/elicitation/request",
            "params": {"serverName": "boxagent_cua", "requestedSchema": {"properties": {}}}})
        self.executor.approve.assert_awaited_once()
        self.executor.send.assert_awaited_once_with({"id": 1, "result": {"action": "decline"}})

    async def test_automatic_approval_does_not_accept_data_requests_or_stopped_tasks(self):
        self.executor.send = AsyncMock()
        for server, properties, stopped in [("other", {}, False),
                ("boxagent_cua", {"password": {"type": "string"}}, False), ("boxagent_cua", {}, True)]:
            self.executor.stopped = stopped
            await self.executor.callback({"id": 1, "method": "mcpServer/elicitation/request",
                "params": {"serverName": server, "requestedSchema": {"properties": properties}}})
            self.executor.send.assert_awaited_with({"id": 1, "result": {"action": "decline"}})

    def test_screenshot_is_delivered_to_model(self):
        result = model_content({"content": [{"type": "image", "mimeType": "image/png", "data": "AAAA"}]}, 2)
        self.assertEqual(result[-1], {"type": "inputImage", "imageUrl": "data:image/png;base64,AAAA"})

    async def test_tool_response_preserves_original_goal_after_observation(self):
        self.executor.goal = "帮我使用 vscode 新建一个测试文件"
        self.executor.bind_tools([{"name": "get_app_state", "inputSchema": {"type": "object"}}])
        result = await self.executor.dynamic("desktop_get_app_state", {"app": "Code"})
        self.assertIn(self.executor.goal, result["contentItems"][-1]["text"])
        self.assertIn("当前观察步骤为 1", result["contentItems"][-1]["text"])
        self.assertEqual(result["contentItems"][1]["text"], "当前界面")

    def test_unsupported_blocked_report_after_actions_is_not_shown(self):
        self.executor.observations[17] = {"step": 17, "success": True}
        self.executor.agent_text = json.dumps({"outcome": "blocked", "summary":
            "未收到具体操作目标，请告诉我需要在桌面应用中完成什么任务。", "evidence_steps": []})
        with self.assertRaises(TaskFailure) as failure:
            self.executor.final_result()
        self.assertEqual(failure.exception.code, "missing_result_evidence")
        self.assertNotIn("未收到具体操作目标", str(failure.exception))
        self.assertIn(self.executor.agent_text, failure.exception.detail)

    def test_blocked_with_observed_obstacle_is_preserved(self):
        self.executor.observations[1] = {"step": 1, "success": True}
        self.executor.agent_text = json.dumps({"outcome": "blocked", "summary": "需要登录", "evidence_steps": [1]})
        self.assertEqual(self.executor.final_result()["summary"], "需要登录")

    def test_completion_without_actual_evidence_is_rejected(self):
        self.executor.agent_text = json.dumps({"outcome": "completed", "summary": "播放了", "evidence_steps": [10]})
        with self.assertRaises(TaskFailure) as failure:
            self.executor.final_result()
        self.assertIn("不存在的观察", failure.exception.detail)

    async def test_failed_tool_observation_preserves_real_failure(self):
        self.executor.bind_tools([{"name": "get_app_state", "inputSchema": {"type": "object"}}])
        self.executor.rpc = AsyncMock(return_value={"isError": True, "content": [{"type": "text", "text": "bootstrapTimedOut"}]})
        await self.executor.dynamic("desktop_get_app_state", {"app": "Code"})
        self.executor.agent_text = json.dumps({"outcome": "blocked", "summary": "连接失败", "evidence_steps": [1]})
        result = self.executor.final_result()
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("电脑操作服务连接超时", result["summary"])
        self.assertFalse(result["evidence"][0]["success"])
        self.assertTrue((self.executor.output / "step-1.txt").exists())

    def test_failed_observation_cannot_prove_success(self):
        self.executor.observations[1] = {"step": 1, "success": False}
        self.executor.agent_text = json.dumps({"outcome": "completed", "summary": "完成", "evidence_steps": [1]})
        with self.assertRaises(TaskFailure):
            self.executor.final_result()

    async def test_exception_still_writes_terminal_result_and_traceback(self):
        self.executor._run = AsyncMock(side_effect=RuntimeError("测试异常"))
        with self.assertRaises(RuntimeError):
            await self.executor.run("测试目标", lambda *_: None, None)
        result = json.loads((self.executor.output / "result.json").read_text())
        status = json.loads((self.executor.output / "status.json").read_text())
        self.assertEqual(result["outcome"], "failed")
        self.assertTrue(result["executor_exited"])
        self.assertFalse(status["running"])
        self.assertIn("traceback", (self.executor.output / "events.jsonl").read_text())

    async def test_cancel_still_writes_terminal_result(self):
        self.executor._run = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.executor.run("测试目标", lambda *_: None, None)
        result = json.loads((self.executor.output / "result.json").read_text())
        self.assertEqual(result["outcome"], "cancelled")

    def test_sensitive_fields_and_inline_images_are_redacted(self):
        data = redact({"api_key": "private", "text": "Bearer private data:image/png;base64,AAAA"})
        self.assertNotIn("private", json.dumps(data))
        self.assertNotIn("base64", data["text"])
