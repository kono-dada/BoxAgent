"""只验证有风险的并发边界，不把真实系统验证替换成假成功。"""

import asyncio
import json
import unittest

from boxagent.infrastructure.runtimes.qwen.realtime import INSTRUCTIONS, TOOLS, QwenRealtimeSession
from boxagent.application.assistant import BoxAgentApplication
from boxagent.core.states import Snapshot, presentation_state


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


class FakeMemory:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def remember(self, observations):
        self.calls.append(("remember", observations))
        return {"admitted": 1, "rejected": 0, "memory_count": 1}

    async def query(self, question, *, top_k=5):
        self.calls.append(("query", question, top_k))
        return {"memories": [{"id": "memory-1", "content": "用户喜欢简洁回答",
                              "timestamp": None, "metadata": {"private": "not exposed"}}],
                "trace": {"controller": "fixture"}}

    async def inspect(self, *, query="", selected_id=None, node_limit=100, edge_limit=200):
        self.calls.append(("inspect", query, selected_id, node_limit, edge_limit))
        return {"nodes": [{"id": "memory-1", "type": "EVENT",
                           "content": "用户喜欢简洁回答", "timestamp": None,
                           "source": "voice_explicit"}],
                "edges": [], "selected_id": selected_id, "truncated": False,
                "statistics": {"node_count": 1, "edge_count": 0,
                               "matched_count": 1, "node_types": {"EVENT": 1},
                               "link_types": {}}}

    async def forget(self, memory_ids):
        self.calls.append(("forget", memory_ids))
        return {"deleted": [{"id": "memory-1", "content": "用户喜欢简洁回答",
                             "timestamp": None, "metadata": {"private": "not exposed"}}],
                "missing": [], "memory_count": 0}

    async def close(self):
        self.closed = True


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.runtime = BoxAgentApplication(self.events.append, HeldExecutor, None)

    async def asyncTearDown(self):
        await self.runtime.cancel_task()

    async def start_job(self):
        accepted = await self.runtime.handle_tool(
            "run_task", {"goal": "帮我打开哔哩哔哩app然后随机点开播放一个视频"})
        await self.runtime.executor.started.wait()
        return accepted

    async def test_status_remains_responsive_while_tool_waits(self):
        accepted = await self.start_job()
        result = await asyncio.wait_for(self.runtime.handle_tool("task_status", {}), .1)
        self.assertEqual(result["status"], "running")
        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(self.runtime.job.done())
        self.runtime.executor.release.set()
        self.assertEqual((await self.runtime.job)["status"], "succeeded")

    async def test_voice_disconnect_does_not_cancel_task(self):
        await self.start_job()
        self.assertFalse(self.runtime.job.done())
        self.runtime.executor.release.set()
        await self.runtime.job
        self.assertEqual(self.runtime.state.task, "succeeded")

    async def test_cancel_stops_executor_and_never_reports_success(self):
        accepted = await self.start_job()
        result = await self.runtime.handle_tool("cancel_task", {})
        self.assertTrue(self.runtime.executor.stopped)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(self.runtime.last_result["status"], "cancelled")

    async def test_busy_does_not_spawn_second_executor(self):
        await self.start_job()
        old = self.runtime.executor
        result = await self.runtime.handle_tool("run_task", {"goal": "打开计算器"})
        self.assertEqual(result["status"], "busy")
        self.assertIs(self.runtime.executor, old)
        await self.runtime.cancel_task()

    def test_speaking_has_priority_without_losing_background_state(self):
        state = Snapshot(task="running", speaking=True)
        self.assertEqual(presentation_state(state), "speaking")
        state.speaking = False
        self.assertEqual(presentation_state(state), "working")

    async def test_goal_is_forwarded_verbatim_without_classification(self):
        await self.start_job()
        self.assertEqual(self.runtime.executor.goal, "帮我打开哔哩哔哩app然后随机点开播放一个视频")
        await self.runtime.cancel_task()

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
        self.runtime.interaction_service.voice = ClosingVoice()
        self.runtime.interaction_service.task = asyncio.create_task(session())
        await self.runtime.stop_voice()
        self.assertTrue(cleanup_finished.is_set())
        self.assertFalse(self.runtime.voice_task.cancelled())

    async def test_memory_tools_keep_voice_payload_small_and_use_exact_ids(self):
        memory = FakeMemory()
        self.runtime.memory_service.backend = memory
        remembered = await self.runtime.handle_tool("remember_memory", {"content": "  用户喜欢简洁回答  "})
        self.assertEqual(remembered["status"], "succeeded")
        self.assertEqual(memory.calls[0][1][0]["content"], "用户喜欢简洁回答")
        self.assertEqual(memory.calls[0][1][0]["metadata"]["source"], "voice_explicit")

        recalled = await self.runtime.handle_tool("recall_memory", {"query": "用户喜欢怎样回答"})
        self.assertEqual(recalled["memories"][0]["id"], "memory-1")
        self.assertNotIn("metadata", recalled["memories"][0])
        self.assertNotIn("trace", recalled)

        forgotten = await self.runtime.handle_tool("forget_memory", {"memory_ids": ["memory-1"]})
        self.assertEqual(forgotten["status"], "succeeded")
        self.assertEqual(memory.calls[-1], ("forget", ["memory-1"]))

    async def test_memory_tools_fail_closed_without_service_or_valid_text(self):
        unavailable = await self.runtime.handle_tool("recall_memory", {"query": "偏好"})
        self.assertEqual(unavailable["status"], "failed")
        self.runtime.memory_service.backend = FakeMemory()
        invalid = await self.runtime.handle_tool("remember_memory", {"content": "  "})
        self.assertEqual(invalid["status"], "failed")
        unobserved = await self.runtime.handle_tool("forget_memory", {"memory_ids": ["memory-1"]})
        self.assertEqual(unobserved["status"], "failed")
        self.assertIn("删除前必须先检索", unobserved["message"])

    async def test_dashboard_snapshot_and_confirmed_exact_delete_use_memory_service(self):
        memory = FakeMemory()
        self.runtime.memory_service.backend = memory
        snapshot = await self.runtime.memory_snapshot(query="简洁", selected_id="memory-1",
                                                      node_limit=20, edge_limit=30)
        self.assertEqual(snapshot["nodes"][0]["id"], "memory-1")
        self.assertEqual(memory.calls[-1], ("inspect", "简洁", "memory-1", 20, 30))
        deleted = await self.runtime.delete_memory_node("memory-1")
        self.assertEqual(deleted["status"], "succeeded")
        self.assertEqual(memory.calls[-1], ("forget", ["memory-1"]))

    async def test_runtime_close_closes_memory_worker(self):
        memory = FakeMemory()
        self.runtime.memory_service.backend = memory
        await self.runtime.close()
        self.assertTrue(memory.closed)


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
    def test_voice_exposes_explicit_memory_tools_and_deletion_guardrail(self):
        names = {item["function"]["name"] for item in TOOLS}
        self.assertTrue({"remember_memory", "recall_memory", "forget_memory"} <= names)
        self.assertIn("只有用户明确说要记住", INSTRUCTIONS)
        self.assertIn("匹配不唯一时先确认", INSTRUCTIONS)

    async def test_barge_in_clears_audio_without_cancelling_task(self):
        calls = []
        voice = QwenRealtimeSession(lambda *args: calls.append(args), lambda *_args, **_kw: None)
        voice.audio, voice.ws = FakeAudio(), FakeSocket()
        voice.response_id, voice.response_active = "old", True
        await voice.receive({"type": "input_audio_buffer.speech_started"})
        await voice.receive({"type": "response.audio.delta", "response_id": "old", "delta": "old audio"})
        self.assertTrue(voice.audio.cleared)
        self.assertFalse(voice.audio.chunks)
        self.assertFalse(calls)

    async def test_session_policy_history_current_query_and_response_are_ordered(self):
        from boxagent.agent.runtime.models import RuntimeMessage

        voice = QwenRealtimeSession(
            lambda *_args: None, lambda *_args, **_kw: None,
            instructions="STABLE POLICY + SOUL",
            environment_context=lambda: "[BoxAgent可信运行环境]\n时区：Asia/Shanghai",
            conversation_history=(
                RuntimeMessage("user", "我偏好简洁回答", sequence=2),
                RuntimeMessage("assistant", "好的", sequence=3),
            ))
        voice.ws = FakeSocket()

        await voice.configure_session({"voice": "test"})
        voice.ready.set()
        await voice.submit_text("现在打开音乐")

        sent = [json.loads(item) for item in voice.ws.sent]
        self.assertEqual(sent[0]["type"], "session.update")
        self.assertEqual(sent[0]["session"]["instructions"], "STABLE POLICY + SOUL")
        self.assertEqual(sent[1:3], [
            {"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "我偏好简洁回答"}]}},
            {"type": "conversation.item.create", "item": {
                "type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "好的"}]}},
        ])
        self.assertEqual(sent[3], {"type": "conversation.item.create", "item": {
            "type": "message", "role": "system",
            "content": [{"type": "input_text",
                         "text": "[BoxAgent可信运行环境]\n时区：Asia/Shanghai"}]}})
        self.assertEqual(sent[4], {"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "现在打开音乐"}]}})
        self.assertEqual(sent[5]["type"], "response.create")

    async def test_checkpoint_is_injected_before_native_history(self):
        from boxagent.agent.runtime.models import RuntimeMessage

        voice = QwenRealtimeSession(
            lambda *_args: None, lambda *_args, **_kw: None,
            conversation_checkpoint="<boxagent_context_checkpoint>{}</boxagent_context_checkpoint>",
            conversation_history=(RuntimeMessage("user", "最近消息", sequence=5),))
        voice.ws = FakeSocket()

        await voice.configure_session({"voice": "test"})

        sent = [json.loads(item) for item in voice.ws.sent]
        self.assertEqual(sent[1]["item"]["role"], "system")
        self.assertIn("boxagent_context_checkpoint",
                      sent[1]["item"]["content"][0]["text"])
        self.assertEqual(sent[2]["item"]["role"], "user")

    async def test_stable_memory_profile_is_injected_before_checkpoint(self):
        voice = QwenRealtimeSession(
            lambda *_args: None, lambda *_args, **_kw: None,
            stable_memory_context="<boxagent_stable_profile>{}</boxagent_stable_profile>",
            conversation_checkpoint="<boxagent_context_checkpoint>{}</boxagent_context_checkpoint>")
        voice.ws = FakeSocket()

        await voice.configure_session({"voice": "test"})

        sent = [json.loads(item) for item in voice.ws.sent]
        self.assertIn("stable_profile", sent[1]["item"]["content"][0]["text"])
        self.assertIn("context_checkpoint", sent[2]["item"]["content"][0]["text"])

    async def test_voice_turn_injects_fresh_environment_when_speech_starts(self):
        snapshots = iter(("env-one", "env-two"))
        voice = QwenRealtimeSession(
            lambda *_args: None, lambda *_args, **_kw: None,
            environment_context=lambda: next(snapshots))
        voice.audio, voice.ws = FakeAudio(), FakeSocket()

        await voice.receive({"type": "input_audio_buffer.speech_started"})
        await voice.receive({"type": "input_audio_buffer.speech_stopped"})
        await voice.receive({"type": "input_audio_buffer.speech_started"})

        sent = [json.loads(item) for item in voice.ws.sent]
        self.assertEqual([
            item["item"]["content"][0]["text"] for item in sent
        ], ["env-one", "env-two"])
        self.assertTrue(all(item["item"]["role"] == "system" for item in sent))

    async def test_environment_capture_failure_does_not_block_text_turn(self):
        traces = []

        def failed_environment():
            raise RuntimeError("clock unavailable")

        voice = QwenRealtimeSession(
            lambda *_args: None, lambda *_args, **_kw: None,
            environment_context=failed_environment,
            trace=lambda kind, **payload: traces.append((kind, payload)))
        voice.ws = FakeSocket()
        voice.ready.set()
        await voice.submit_text("继续聊天")

        sent = [json.loads(item) for item in voice.ws.sent]
        self.assertEqual(sent[0]["item"]["role"], "user")
        self.assertEqual(sent[1]["type"], "response.create")
        self.assertEqual(traces[0][0], "environment.capture_failed")

    async def test_cancelled_response_does_not_publish_assistant_final(self):
        records = []

        async def record(kind, payload):
            records.append((kind, payload))

        voice = QwenRealtimeSession(lambda *_args: None, lambda *_args, **_kw: None,
                          conversation_event=record)
        voice.audio, voice.ws = FakeAudio(), FakeSocket()
        await voice.receive({"type": "response.created", "response": {"id": "old"}})
        await voice.receive({"type": "response.audio_transcript.done",
                             "response_id": "old", "transcript": "尚未送达的回答"})
        await voice.receive({"type": "input_audio_buffer.speech_started"})
        await voice.receive({"type": "response.done", "response": {"id": "old"}})

        self.assertNotIn("assistant_final", [kind for kind, _payload in records])
        done = next(payload for kind, payload in records if kind == "response_done")
        self.assertTrue(done["cancelled"])

    async def test_result_waits_until_user_and_response_finish(self):
        voice = QwenRealtimeSession(None, lambda *_args, **_kw: None)
        voice.ws = FakeSocket()
        voice.user_speaking = True
        await voice.results.put(("real-call", {"status": "succeeded"}))
        worker = asyncio.create_task(voice.deliver_results())
        await asyncio.sleep(.15)
        self.assertFalse(voice.ws.sent)
        voice.user_speaking = False
        await asyncio.sleep(.15)
        self.assertEqual(len(voice.ws.sent), 2)
        self.assertEqual(list(voice.response_origins),
                         [{"kind": "tool_result", "call_id": "real-call"}])
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    async def test_tool_result_response_is_marked_separately_from_user_response(self):
        records = []

        async def record(kind, payload):
            records.append((kind, payload))

        voice = QwenRealtimeSession(lambda *_args: None, lambda *_args, **_kw: None,
                          conversation_event=record)
        voice.response_origins.append(
            {"kind": "tool_result", "call_id": "call-1"})
        await voice.receive({"type": "response.created", "response": {"id": "result-r"}})

        payload = next(payload for kind, payload in records
                       if kind == "response_created")
        self.assertEqual(payload["origin"], "tool_result")
        self.assertEqual(payload["call_id"], "call-1")

    async def test_multiple_requested_responses_keep_their_origins_in_order(self):
        records = []

        async def record(kind, payload):
            records.append((kind, payload))

        voice = QwenRealtimeSession(lambda *_args: None, lambda *_args, **_kw: None,
                          conversation_event=record)
        voice.response_origins.extend([
            {"kind": "tool_result", "call_id": "call-1"},
            {"kind": "user"},
        ])

        await voice.receive({"type": "response.created", "response": {"id": "tool-r"}})
        await voice.receive({"type": "response.created", "response": {"id": "user-r"}})

        created = [payload for kind, payload in records
                   if kind == "response_created"]
        self.assertEqual(created[0]["origin"], "tool_result")
        self.assertEqual(created[0]["call_id"], "call-1")
        self.assertEqual(created[1]["origin"], "user")


if __name__ == "__main__":
    unittest.main()
