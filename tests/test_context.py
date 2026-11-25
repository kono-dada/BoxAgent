"""验证过时上下文丢弃、临时图片清理和独立生命周期。"""

import asyncio
import json
import runpy
import time
import unittest
from concurrent.futures import Future
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from boxagent.application.assistant import BoxAgentApplication
from boxagent.infrastructure.perception.qwen_mlx import (
    WindowSummary,
    focused_window,
    foreground_game,
)


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_launch_forwards_the_optional_vision_bundle(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "python"
            executable.touch()
            bundle = root / "wuwa-coreml"
            for configured_bundle in (None, bundle):
                flags = ("--review-coreml-bundle", str(bundle)) if configured_bundle else ()
                observer = WindowSummary(
                    lambda *_args, **_kwargs: None,
                    workspace=root, model=root, python=executable,
                    vision_bundle=configured_bundle,
                )
                observer.reply = AsyncMock(return_value={"ready": True})
                try:
                    with patch(
                        "boxagent.infrastructure.perception.qwen_mlx.asyncio.create_subprocess_exec",
                        return_value=SimpleNamespace(returncode=0),
                    ) as launch:
                        await observer._ensure_model()
                    observer.reply.assert_awaited_once_with(120)
                    self.assertEqual(launch.call_args.args[7:], flags)
                finally:
                    await observer.stop()

    def test_worker_uses_shared_acceleration_for_plain_and_structured_input(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts/window_summary_worker.py"
        serve = runpy.run_path(str(script))["serve_mlx"]
        common = {"image": "clip.jpg", "analysis_prompt": "描述画面"}
        review = '{"observation":"最右侧的角色跳起。","observed_frames":[5,6],"scene":"fighting"}'
        moment = review.replace("fighting", "moment")
        answers = [
            "可见两个目标。", review, review[:-1], moment, "?", 'unclear"}',
            "未能复盘", "bad", "第一轮原文",
        ]
        generate = Mock(side_effect=(
            [SimpleNamespace(text=answer) for answer in answers] + [RuntimeError("retry failed")]
        ))
        draft = object()
        startup: Future[object] = Future()
        startup.set_result(draft)
        requests = [common] + [{**common, "structured_review": True}] * 6
        messages = StringIO("\n".join(map(json.dumps, requests)) + "\n")
        output = StringIO()
        with (
            patch("sys.stdin", messages), patch("sys.stdout", output),
            patch.dict(serve.__globals__, {"summarize_image": generate}),
            patch.dict("sys.modules", {"wuwa_cpu_draft": SimpleNamespace(ReviewCheckpoint=object)}),
        ):
            serve(object(), object(), {"model_type": "qwen3_5"}, draft_startup=startup)
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], [
            {"ready": True}, {"summary": "可见两个目标。"},
            {"summary": review}, {"summary": review}, {"summary": moment},
            {"summary": '{"scene":"unclear"}'}, {"summary": "未能复盘"}, {"summary": "第一轮原文"},
        ])
        self.assertTrue(all(call.kwargs["cpu_draft"] is draft for call in generate.call_args_list))
        checkpoints = [call.kwargs["checkpoint"] for call in generate.call_args_list]
        self.assertIsNone(checkpoints[0])
        self.assertEqual(len(set(checkpoints[1:])), 6)
        self.assertEqual(checkpoints[4::2], checkpoints[5::2])
        self.assertEqual(
            [call.kwargs.get("prefix", "") for call in generate.call_args_list],
            ["", "", "", "", "", '{"scene":"', "", '{"scene":"', "", '{"scene":"'],
        )

    def test_focused_window_accepts_an_unlisted_fullscreen_game(self) -> None:
        front = SimpleNamespace(
            bundleIdentifier=lambda: "example.unlisted.game",
            processIdentifier=lambda: 42,
            localizedName=lambda: "Unlisted Game",
        )
        appkit = SimpleNamespace(NSWorkspace=Mock())
        appkit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value = front
        quartz = SimpleNamespace(CGShieldingWindowLevel=lambda: 2147483628)
        fullscreen = {"id": 12, "app": "Unlisted Game", "layer": 2147483628}
        with (
            patch.dict("sys.modules", {"AppKit": appkit, "Quartz": quartz}),
            patch("boxagent.infrastructure.perception.qwen_mlx.sys.platform", "darwin"),
            patch("boxagent.infrastructure.perception.qwen_mlx.os.getpid", return_value=1),
            patch(
                "boxagent.infrastructure.perception.qwen_mlx._mac_window",
                side_effect=[None, fullscreen],
            ) as window,
        ):
            self.assertEqual(focused_window(), fullscreen)
            self.assertEqual([call.args[-1] for call in window.call_args_list],
                             [{0}, {2147483628}])

    def test_foreground_dock_recognizes_only_the_real_lol_shield_window(self) -> None:
        bundle = Mock(return_value="com.riotgames.LeagueofLegends.GameClient")
        front = SimpleNamespace(
            bundleIdentifier=bundle,
            processIdentifier=lambda: 42,
            localizedName=lambda: "League Of Legends",
        )
        appkit = SimpleNamespace(NSWorkspace=Mock())
        appkit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value = front
        quartz = SimpleNamespace(
            CGShieldingWindowLevel=lambda: 2147483628,
            CGWindowLevelForKey=lambda _: 1000,
            kCGScreenSaverWindowLevelKey=13,
        )
        rect = {"x": 0, "y": 0, "width": 1920, "height": 1200}
        with (
            patch.dict("sys.modules", {"AppKit": appkit, "Quartz": quartz}),
            patch("boxagent.infrastructure.perception.qwen_mlx.sys.platform", "darwin"),
            patch(
                "boxagent.infrastructure.perception.qwen_mlx._mac_window",
                return_value={"id": 7, "layer": 2147483628, "bounds": rect},
            ) as window,
        ):
            self.assertEqual(
                foreground_game(), {"game": "lol", "bounds": rect, "shield_offset": 2147482629}
            )
            window.assert_called_once_with(42, "League Of Legends", bundle(), {0, 2147483628})
            bundle.return_value = "com.riotgames.LeagueofLegends.LeagueClientUx"
            window.return_value = None
            self.assertIsNone(foreground_game())
            self.assertEqual(window.call_args.args[-1], {0})

    async def test_lol_mode_rejects_previously_captured_browser_reference(self) -> None:
        from boxagent.bootstrap.engine import current_screen_reference
        from boxagent.core.states import Snapshot

        window = {"id": 1, "app": "Browser"}
        state = Snapshot(context_enabled=True, context_text="浏览器页面",
                         context_at=time.time(), context_app="Browser", context_window_id=1,
                         lol_companion_enabled=True)
        with patch("boxagent.infrastructure.perception.focused_window", return_value=window):
            self.assertEqual(current_screen_reference(state), "")
            state.lol_companion_enabled = False
            self.assertIn("浏览器页面", current_screen_reference(state))

    async def test_lol_mode_never_captures_other_foreground_windows(self) -> None:
        events = []
        observer = WindowSummary(lambda kind, **data: events.append((kind, data)),
            window_allowed=lambda w: w["app"] == "League of Legends")
        observer.capture = AsyncMock()
        with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                   return_value={"id": 1, "app": "Browser"}):
            await observer.observe()
        observer.capture.assert_not_awaited()
        self.assertEqual(events[-1][0], "context.waiting")
        self.assertEqual(events[-1][1]["context_text"], "")

    async def test_lol_scope_change_during_inference_discards_other_window(self) -> None:
        events = []
        game_only = False
        observer = WindowSummary(
            lambda kind, **data: events.append((kind, data)),
            window_allowed=lambda w: not game_only or w["app"] == "League of Legends",
        )
        observer.process = SimpleNamespace(
            returncode=None, stdin=SimpleNamespace(write=Mock(), drain=AsyncMock())
        )

        async def reply(timeout):
            nonlocal game_only
            game_only = True
            return {"summary": "浏览器文字。"}

        async def capture(window_id, path):
            Image.new("RGB", (100, 100)).save(path)

        observer.capture, observer.reply = capture, reply
        with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                   return_value={"id": 1, "app": "Browser"}):
            await observer.observe()
        self.assertEqual(events[-1][0], "context.stale")
        self.assertFalse(any(kind == "context.updated" for kind, _ in events))

    async def test_companion_chat_cannot_recycle_previous_screen_claims(self) -> None:
        events = []
        observer = WindowSummary(lambda kind, **fields: events.append((kind, fields)))
        observer.capture = AsyncMock()
        window = {"id": 7, "app": "electron", "title": "BoxAgent · 对话"}
        with patch(
            "boxagent.infrastructure.perception.qwen_mlx.focused_window", return_value=window
        ):
            await observer.observe()
        observer.capture.assert_not_awaited()
        kind, fields = events[-1]
        self.assertEqual(kind, "context.updated")
        self.assertIn("桌宠自身窗口", fields["context_text"])
        self.assertEqual(fields["context_window_id"], 7)

    async def test_capture_is_deleted_and_switched_window_result_discarded(self):
        for switched in (False, True):
            events, paths = [], []
            observer = WindowSummary(
                lambda kind, _events=events, **fields: _events.append((kind, fields))
            )
            observer.process = SimpleNamespace(
                returncode=None, stdin=SimpleNamespace(write=Mock(), drain=AsyncMock())
            )
            observer.reply = AsyncMock(return_value={"summary": "正在阅读代码"})

            async def capture(*args: Path, saved_paths: list[Path] = paths) -> None:
                path = Path(args[-1])
                saved_paths.append(path)
                Image.new("RGB", (1600, 900)).save(path)

            observer.capture = capture
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
    async def test_capture_timeout_is_retried_and_process_is_reaped(self):
        events = []
        observer = WindowSummary(lambda kind, **fields: events.append(kind))
        observer.process = SimpleNamespace(
            returncode=None, stdin=SimpleNamespace(write=Mock(), drain=AsyncMock())
        )
        observer.reply = AsyncMock(return_value={"summary": "正在阅读代码。"})
        process = SimpleNamespace(returncode=None, communicate=AsyncMock(),
                                  kill=Mock(), wait=AsyncMock())
        window = {"id": 7, "app": "测试窗口"}

        async def timeout(awaitable, seconds):
            awaitable.close()
            raise TimeoutError

        with (
            patch(
                "boxagent.infrastructure.perception.qwen_mlx.focused_window", return_value=window
            ),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
            patch("asyncio.wait_for", timeout),
            patch("boxagent.infrastructure.perception.qwen_mlx.sys.platform", "darwin"),
        ):
            await observer.observe()
        self.assertEqual(events[-1], "context.waiting")
        self.assertEqual(observer.capture_failures, 1)
        process.kill.assert_called_once()
        process.wait.assert_awaited_once()

        async def capture(*args):
            Image.new("RGB", (400, 300)).save(Path(args[-1]))

        observer.capture = capture
        with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                   return_value=window):
            await observer.observe()
        self.assertEqual(events[-1], "context.updated")
        self.assertEqual(observer.capture_failures, 0)

    async def test_mixed_capture_failures_share_the_retry_limit(self):
        events = []
        observer = WindowSummary(lambda kind, **fields: events.append(kind))
        observer.capture = AsyncMock(side_effect=[
            RuntimeError("could not create image from window"),
            TimeoutError(), OSError("capture unavailable")])
        with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                   return_value={"id": 7, "app": "测试窗口"}):
            await observer.observe()
            await observer.observe()
            self.assertEqual(events[-1], "context.waiting")
            with self.assertRaises(OSError):
                await observer.observe()
        self.assertEqual(observer.capture_failures, 3)

    async def test_capture_cancellation_is_not_a_retry(self):
        observer = WindowSummary(lambda kind, **fields: None)
        observer.capture = AsyncMock(side_effect=asyncio.CancelledError)
        with (
            patch("boxagent.infrastructure.perception.qwen_mlx.focused_window",
                  return_value={"id": 7, "app": "测试窗口"}),
            self.assertRaises(asyncio.CancelledError),
        ):
            await observer.observe()
        self.assertEqual(observer.capture_failures, 0)

    async def test_transient_capture_failure_skips_round_until_limit(self):
        from boxagent.infrastructure.perception.qwen_mlx import MAX_CAPTURE_FAILURES

        events = []
        observer = WindowSummary(lambda kind, **fields: events.append(kind))
        observer.capture = AsyncMock(side_effect=RuntimeError(
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
        observer.process = SimpleNamespace(
            returncode=None, stdin=SimpleNamespace(write=Mock(), drain=AsyncMock())
        )
        observer.reply = AsyncMock(return_value={"summary": "正在阅读代码。"})
        failures = [RuntimeError("could not create image from window")]

        async def capture(*args):
            if failures:
                raise failures.pop()
            Image.new("RGB", (400, 300)).save(Path(args[-1]))

        observer.capture = capture
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

        self.assertEqual(
            clean_summary("界面显示一个文档。当前界面显示“Agent"), "界面显示一个文档。"
        )
        self.assertEqual(clean_summary("只有半句没有句号"), "只有半句没有句号")
