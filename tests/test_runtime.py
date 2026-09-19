"""只验证有风险的并发边界，不把真实系统验证替换成假成功。"""

import asyncio
import unittest

from boxagent.domain import Snapshot, presentation_state
from boxagent.runtime import Runtime
from boxagent.voice import QwenVoice


class HeldExecutor:
    def __init__(self, task_id):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.stopped = False

    async def run(self, goal, progress, approve):
        self.goal = goal
        self.started.set()
        await self.release.wait()
        return {"outcome": "completed", "summary": "已经完成用户目标", "evidence": []}

    async def cancel(self):
        self.stopped = True


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.runtime = Runtime(self.events.append, HeldExecutor, None)

    async def asyncTearDown(self):
        await self.runtime.cancel_task()

    async def start_job(self):
        caller = asyncio.create_task(self.runtime.handle_tool("run_task", {"goal": "帮我打开哔哩哔哩app然后随机点开播放一个视频"}))
        await asyncio.sleep(0)
        await self.runtime.executor.started.wait()
        return caller

    async def test_status_remains_responsive_while_tool_waits(self):
        caller = await self.start_job()
        result = await asyncio.wait_for(self.runtime.handle_tool("task_status", {}), .1)
        self.assertEqual(result["status"], "running")
        self.assertFalse(caller.done())
        self.runtime.executor.release.set()
        self.assertEqual((await caller)["status"], "succeeded")

    async def test_voice_disconnect_does_not_cancel_task(self):
        caller = await self.start_job()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        self.assertFalse(self.runtime.job.done())
        self.runtime.executor.release.set()
        await self.runtime.job
        self.assertEqual(self.runtime.state.task, "succeeded")

    async def test_cancel_stops_executor_and_never_reports_success(self):
        caller = await self.start_job()
        result = await self.runtime.handle_tool("cancel_task", {})
        self.assertTrue(self.runtime.executor.stopped)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual((await caller)["status"], "cancelled")

    async def test_busy_does_not_spawn_second_executor(self):
        caller = await self.start_job()
        old = self.runtime.executor
        result = await self.runtime.handle_tool("run_task", {"goal": "打开计算器"})
        self.assertEqual(result["status"], "busy")
        self.assertIs(self.runtime.executor, old)
        await self.runtime.cancel_task()
        await caller

    def test_speaking_has_priority_without_losing_background_state(self):
        state = Snapshot(task="running", speaking=True)
        self.assertEqual(presentation_state(state), "speaking")
        state.speaking = False
        self.assertEqual(presentation_state(state), "working")

    async def test_goal_is_forwarded_verbatim_without_classification(self):
        caller = await self.start_job()
        self.assertEqual(self.runtime.executor.goal, "帮我打开哔哩哔哩app然后随机点开播放一个视频")
        await self.runtime.cancel_task()
        await caller

    async def test_text_submission_returns_immediately_without_voice(self):
        goal = "帮我打开哔哩哔哩，随机播放一个视频"
        accepted = await asyncio.wait_for(self.runtime.submit_text(goal), .1)
        self.assertEqual(accepted["status"], "accepted")
        await self.runtime.executor.started.wait()
        self.assertEqual(self.runtime.executor.goal, goal)
        self.assertEqual(self.runtime.state.user_text, goal)
        self.assertEqual(self.runtime.state.voice, "off")
        self.assertFalse(self.runtime.job.done())
        self.runtime.executor.release.set()
        await self.runtime.job
        self.assertEqual(self.runtime.state.task, "succeeded")

    async def test_busy_text_does_not_overwrite_current_request(self):
        await self.runtime.submit_text("第一个目标")
        result = await self.runtime.submit_text("第二个目标")
        self.assertEqual(result["status"], "busy")
        self.assertEqual(self.runtime.state.user_text, "第一个目标")

    async def test_blank_text_does_not_create_executor(self):
        result = await self.runtime.submit_text("  \n ")
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(self.runtime.executor)

    async def test_blocked_is_not_reported_as_success(self):
        caller = await self.start_job()
        async def blocked(*_args):
            return {"outcome": "blocked", "summary": "需要先登录"}
        await self.runtime.cancel_task()
        await caller
        self.runtime.executor.run = blocked
        await self.runtime.run_job("打开应用")
        self.assertEqual(self.runtime.last_result["status"], "blocked")

    async def test_voice_shutdown_waits_for_resource_cleanup(self):
        cleanup_started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        class ClosingVoice:
            async def stop(self):
                cleanup_started.set()
        async def session():
            await cleanup_started.wait()
            await asyncio.sleep(.05)
            cleanup_finished.set()
        self.runtime.voice = ClosingVoice()
        self.runtime.voice_task = asyncio.create_task(session())
        await self.runtime.stop_voice()
        self.assertTrue(cleanup_finished.is_set())
        self.assertFalse(self.runtime.voice_task.cancelled())


class FakeAudio:
    def __init__(self):
        self.cleared = False
        self.chunks = []

    def clear(self):
        self.cleared = True

    def append(self, data):
        self.chunks.append(data)


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_barge_in_clears_audio_without_cancelling_task(self):
        calls = []
        voice = QwenVoice(lambda *args: calls.append(args), lambda *_args, **_kw: None)
        voice.audio, voice.ws = FakeAudio(), FakeSocket()
        voice.response_id, voice.response_active = "old", True
        await voice.receive({"type": "input_audio_buffer.speech_started"})
        await voice.receive({"type": "response.audio.delta", "response_id": "old", "delta": "old audio"})
        self.assertTrue(voice.audio.cleared)
        self.assertFalse(voice.audio.chunks)
        self.assertFalse(calls)

    async def test_result_waits_until_user_and_response_finish(self):
        voice = QwenVoice(None, lambda *_args, **_kw: None)
        voice.ws = FakeSocket()
        voice.user_speaking = True
        await voice.results.put(("real-call", {"status": "succeeded"}))
        worker = asyncio.create_task(voice.deliver_results())
        await asyncio.sleep(.15)
        self.assertFalse(voice.ws.sent)
        voice.user_speaking = False
        await asyncio.sleep(.15)
        self.assertEqual(len(voice.ws.sent), 2)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
