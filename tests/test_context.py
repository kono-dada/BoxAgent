"""验证过时上下文丢弃、临时图片清理和独立生命周期。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from boxagent.infrastructure.perception.qwen_mlx import WindowSummary
from boxagent.application.assistant import BoxAgentApplication


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_is_deleted_and_switched_window_result_discarded(self):
        for switched in (False, True):
            events, paths = [], []
            observer = WindowSummary(lambda kind, **fields: events.append((kind, fields)))
            observer.process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()))
            observer.reply = AsyncMock(return_value={"summary": "正在阅读代码"})

            async def capture(*args):
                path = Path(args[-1])
                paths.append(path)
                Image.new("RGB", (1600, 900)).save(path)

            observer.command = capture
            first = {"id": 1, "app": "测试窗口"}
            second = {"id": 2, "app": "另一个窗口"} if switched else first
            with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                       side_effect=[first, second]):
                await observer.observe()
            self.assertEqual(events[-1][0], "context.stale" if switched else "context.updated")
            self.assertFalse(paths[0].exists())

    async def test_pause_cancels_observer_without_touching_executor(self):
        runtime = BoxAgentApplication(Mock(), Mock(), None)
        observer = WindowSummary(runtime.emit)
        observer.task = asyncio.create_task(asyncio.sleep(60))
        runtime.observer = observer
        await runtime.toggle_context()
        self.assertIsNone(observer.task)
        self.assertEqual(runtime.state.task, "idle")
        runtime.execution_service.executor_factory.assert_not_called()
        self.assertEqual(runtime.state.context_status, "屏幕总结已关闭")


class CaptureFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_capture_failure_skips_round_until_limit(self):
        from boxagent.infrastructure.perception.qwen_mlx import MAX_CAPTURE_FAILURES

        events = []
        observer = WindowSummary(lambda kind, **fields: events.append(kind))
        observer.command = AsyncMock(side_effect=RuntimeError(
            "could not create image from window\n"))
        window = {"id": 7, "app": "测试窗口"}
        with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                   return_value=window):
            for _ in range(MAX_CAPTURE_FAILURES - 1):
                await observer.observe()
            self.assertEqual(events[-1], "context.waiting")
            with self.assertRaises(RuntimeError):
                await observer.observe()

    async def test_successful_capture_resets_failure_count(self):
        observer = WindowSummary(lambda kind, **fields: None)
        observer.process = SimpleNamespace(stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()))
        observer.reply = AsyncMock(return_value={"summary": "正在阅读代码。"})
        failures = [RuntimeError("could not create image from window")]

        async def capture(*args):
            if failures:
                raise failures.pop()
            Image.new("RGB", (400, 300)).save(Path(args[-1]))

        observer.command = capture
        window = {"id": 7, "app": "测试窗口"}
        with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                   return_value=window):
            await observer.observe()
            self.assertEqual(observer.capture_failures, 1)
            await observer.observe()
        self.assertEqual(observer.capture_failures, 0)


class SummaryCleanupTests(unittest.TestCase):
    def test_repeated_sentences_are_removed_in_order(self):
        from boxagent.infrastructure.perception.qwen_mlx import clean_summary

        text = ("界面显示一个文档。文档中提到了A和B。文档中提到了A和B。"
                "文档中提到了A和B。右侧是目录。")
        self.assertEqual(clean_summary(text), "界面显示一个文档。文档中提到了A和B。右侧是目录。")

    def test_truncated_tail_is_dropped_but_single_fragment_kept(self):
        from boxagent.infrastructure.perception.qwen_mlx import clean_summary

        self.assertEqual(clean_summary("界面显示一个文档。当前界面显示“Agent"), "界面显示一个文档。")
        self.assertEqual(clean_summary("只有半句没有句号"), "只有半句没有句号")
