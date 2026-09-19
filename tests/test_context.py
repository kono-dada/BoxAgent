"""验证过时上下文丢弃、临时图片清理和独立生命周期。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
from boxagent.context import WindowSummary
from boxagent.runtime import Runtime


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
            with patch("boxagent.context.focused_window", side_effect=[first, second]):
                await observer.observe()
            self.assertEqual(events[-1][0], "context.stale" if switched else "context.updated")
            self.assertFalse(paths[0].exists())

    async def test_pause_cancels_observer_without_touching_executor(self):
        runtime = Runtime(Mock(), Mock(), None)
        observer = WindowSummary(runtime.emit)
        observer.task = asyncio.create_task(asyncio.sleep(60))
        runtime.observer = observer
        await runtime.toggle_context()
        self.assertIsNone(observer.task)
        self.assertEqual(runtime.state.task, "idle")
        runtime.executor_factory.assert_not_called()
        self.assertEqual(runtime.state.context_status, "屏幕总结已关闭")
