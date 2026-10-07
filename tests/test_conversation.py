"""Product Session persistence and Runtime context contracts."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.application.assistant import BoxAgentApplication
from boxagent.core.events import StateEvents
from boxagent.application.context_checkpoint import ContextCheckpointCoordinator
from boxagent.domain.conversation import (
    ContextCheckpoint,
    ConversationService,
    ProductEvent,
    RuntimeBinding,
)
from boxagent.domain.interaction.service import InteractionService
from boxagent.agent.harness.context import RuntimeContextProjector
from boxagent.agent.harness.request import HarnessInput
from boxagent.agent.harness.persona import Persona, load_persona
from boxagent.agent.harness.request_builder import RuntimeRequestBuilder


class JsonlSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_session_replaces_previous_session_task_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            published = []
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            application = BoxAgentApplication(
                published.append, lambda *_args, **_kwargs: None, None,
                conversation_service=conversation)
            await application.start()
            application.events.emit(
                "task.succeeded", task="succeeded", task_id="old-task",
                task_text="上一会话已完成", user_text="旧请求",
                assistant_text="旧回复", task_started_at=1,
                task_ended_at=2, task_activity_at=2,
                notification_id="old-notification",
                notification_text="旧提醒", notification_unread=True)

            created = await application.create_session("新的会话")
            state = published[-1]["state"]

            self.assertEqual(state["session_id"], created["session_id"])
            self.assertEqual(state["task"], "idle")
            self.assertEqual(state["task_id"], "")
            self.assertEqual(state["task_text"], "")
            self.assertEqual(state["user_text"], "")
            self.assertEqual(state["assistant_text"], "")
            self.assertFalse(state["notification_unread"])
            await application.close()

    async def test_new_session_disconnects_runtime_bound_to_previous_session(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            application = BoxAgentApplication(
                lambda _event: None, lambda *_args, **_kwargs: None, None,
                conversation_service=conversation)
            await application.start()
            application.interaction_service.reset_session = AsyncMock()

            await application.create_session("隔离上下文")

            application.interaction_service.reset_session.assert_awaited_once()

    async def test_voice_toggle_waits_for_pending_utterance_before_stopping(self):
        stopped = asyncio.Event()

        class Voice:
            finish_pending_input = AsyncMock(return_value=True)

            async def stop(self):
                stopped.set()

        service = InteractionService(
            StateEvents(lambda _event: None), None, AsyncMock())
        service.voice = Voice()
        service.connection_microphone = True
        service.task = asyncio.create_task(stopped.wait())

        await service.toggle()

        Voice.finish_pending_input.assert_awaited_once()
        self.assertTrue(stopped.is_set())

    async def test_text_realtime_idle_timeout_is_silent_and_reconnectable(self):
        events = []

        class IdleVoice:
            async def run(self):
                raise RuntimeError(
                    "received 1007: Your session was closed because no user input "
                    "was received for 180 seconds.")

        service = InteractionService(
            StateEvents(events.append), None, AsyncMock())
        voice = IdleVoice()
        service.voice = voice
        service.connection_microphone = False

        await service._run(voice, microphone=False)

        self.assertEqual(events[-1]["type"], "front.idle_disconnected")
        self.assertEqual(events[-1]["state"]["error"], "")
        self.assertIsNone(service.voice)

    async def test_provider_response_idle_close_is_silent_for_open_microphone(self):
        events = []

        class IdleVoice:
            async def run(self):
                raise RuntimeError(
                    "received 1007 (invalid frame payload data) Your session "
                    "was closed because no response was generated for 180 "
                    "seconds.; then sent 1007 (invalid frame payload data)")

        service = InteractionService(
            StateEvents(events.append), None, AsyncMock())
        voice = IdleVoice()
        service.voice = voice
        service.connection_microphone = True

        await service._run(voice, microphone=True)

        self.assertNotIn("voice.error", [event["type"] for event in events])
        self.assertEqual(events[-1]["type"], "voice.off")
        self.assertEqual(events[-1]["state"]["error"], "")

    async def test_aoq_disconnect_reconnects_without_showing_transient_error(self):
        events = []
        disconnect = asyncio.Event()
        keep_connected = asyncio.Event()
        voices = []

        class Voice:
            def __init__(self, first):
                self.first = first
                self.ready = asyncio.Event()

            async def run(self):
                self.ready.set()
                if self.first:
                    await disconnect.wait()
                    raise RuntimeError("AOQ 语音连接已断开")
                await keep_connected.wait()

            async def stop(self):
                keep_connected.set()

        def factory(*_args, **_kwargs):
            voice = Voice(first=not voices)
            voices.append(voice)
            return voice

        service = InteractionService(
            StateEvents(events.append), factory, AsyncMock(),
            reconnect_delays=(0, 0, 0))
        await service.toggle()
        disconnect.set()

        for _attempt in range(20):
            if len(voices) == 2 and service.is_connected:
                break
            await asyncio.sleep(.01)

        self.assertEqual(len(voices), 2)
        self.assertTrue(service.is_connected)
        self.assertNotIn("voice.error", [event["type"] for event in events])
        self.assertIn("voice.reconnecting", [event["type"] for event in events])
        await service.close()

    async def test_default_session_round_trip_and_duplicate_event_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlSessionStore(Path(directory) / "conversations")
            service = ConversationService(store)
            session = await service.start()
            context = await service.begin_interaction(
                "请记住我喜欢简洁回答", task_id="task-1")
            await service.finish_interaction(
                context, status="succeeded", assistant_content="好的")

            reopened = ConversationService(JsonlSessionStore(store.root))
            active = await reopened.start()
            events = await reopened.events(session.session_id)

            self.assertEqual(active.session_id, session.session_id)
            self.assertEqual([item.type for item in events], [
                "interaction.started", "message.final", "message.final",
                "interaction.finalized"])
            self.assertEqual([item.sequence for item in events], [1, 2, 3, 4])
            self.assertEqual(active.title, "请记住我喜欢简洁回答")

            duplicate = ProductEvent(
                sequence=0, event_id=events[1].event_id, type="message.final",
                session_id=session.session_id, occurred_at=2,
                interaction_id=context.interaction_id, role="user", content="不同")
            stored = await reopened.store.append_event(duplicate)
            self.assertEqual(stored.content, "请记住我喜欢简洁回答")
            self.assertEqual(len(await reopened.events(session.session_id)), 4)

    async def test_truncated_last_line_is_repaired_before_append(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlSessionStore(Path(directory) / "conversations")
            session = await store.create_session()
            event = ProductEvent(0, "evt_1", "message.final", session.session_id,
                                 1.0, role="user", content="第一条")
            await store.append_event(event)
            path = store.root / "sessions" / session.session_id / "events.jsonl"
            with path.open("a", encoding="utf-8") as stream:
                stream.write('{"broken":')
            second = ProductEvent(0, "evt_2", "message.final", session.session_id,
                                  2.0, role="assistant", content="第二条")
            await store.append_event(second)
            events = await store.read_events(session.session_id)
            self.assertEqual([item.sequence for item in events], [1, 2])
            self.assertEqual(len(path.read_text().splitlines()), 2)

    async def test_sessions_are_manual_and_runtime_bindings_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlSessionStore(Path(directory) / "conversations")
            first = await store.create_session("第一条")
            second = await store.create_session("第二条")
            await store.save_runtime_binding(first.session_id, RuntimeBinding(
                runtime="codex", provider="deepseek", model="deepseek-flash",
                thread_id="thread-a", context_cursor=8))
            await store.activate_session(first.session_id)

            self.assertEqual((await store.active_session()).session_id, first.session_id)
            self.assertEqual((await store.runtime_binding(
                first.session_id, "codex")).thread_id, "thread-a")
            self.assertIsNone(await store.runtime_binding(second.session_id, "codex"))

            await store.archive_session(first.session_id)
            self.assertEqual((await store.active_session()).session_id, second.session_id)

    async def test_checkpoint_and_segment_round_trip_in_append_only_context_log(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlSessionStore(Path(directory) / "conversations")
            session = await store.create_session()
            checkpoint = ContextCheckpoint(
                checkpoint_id="ckp_test", session_id=session.session_id,
                runtime="qwen_realtime", source_from_sequence=1,
                covered_through_sequence=4, source_hash="abc", created_at=3.0,
                provider="deepseek", model="deepseek-flash",
                content={"summary": "用户在做 BoxAgent", "user_facts": [],
                         "decisions": [], "open_loops": []})
            from boxagent.domain.conversation import RuntimeContextSegment
            segment = RuntimeContextSegment(
                segment_id="seg_test", session_id=session.session_id,
                runtime="qwen_realtime", start_sequence=1, end_sequence=4,
                checkpoint_id="ckp_test", created_at=3.0)

            await store.append_checkpoint(checkpoint, segment)
            reopened = JsonlSessionStore(store.root)

            self.assertEqual((await reopened.latest_checkpoint(
                session.session_id, "qwen_realtime")).source_hash, "abc")
            self.assertEqual((await reopened.context_segments(
                session.session_id, "qwen_realtime"))[0].checkpoint_id,
                             "ckp_test")
    async def test_start_marks_unfinished_interaction_as_interrupted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlSessionStore(Path(directory) / "conversations")
            session = await store.create_session()
            await store.append_event(ProductEvent(
                0, "evt_started", "interaction.started", session.session_id, 1.0,
                interaction_id="int_lost", task_id="task_lost", status="running"))

            service = ConversationService(JsonlSessionStore(store.root))
            await service.start()
            await service.start()
            events = await service.events(session.session_id)

            terminal = [item for item in events if item.type == "interaction.finalized"]
            self.assertEqual(len(terminal), 1)
            self.assertEqual(terminal[0].status, "interrupted")

    async def test_text_task_writes_final_product_events(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = {}

            class CompleteExecutor:
                def __init__(self, task_id, **context):
                    captured.update(context)

                async def run(self, goal, progress, approve):
                    return {"outcome": "completed", "summary": "已经打开音乐",
                            "evidence": []}

                async def cancel(self):
                    pass

            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            application = BoxAgentApplication(
                lambda _event: None, CompleteExecutor, None,
                conversation_service=conversation)
            await application.start()
            accepted = await application.submit_text("打开音乐")
            await application.job
            events = await conversation.events(accepted["session_id"])

            self.assertEqual(captured["session_id"], accepted["session_id"])
            self.assertEqual(captured["interaction_id"], accepted["interaction_id"])
            self.assertEqual([item.type for item in events], [
                "interaction.started", "message.final", "message.final",
                "interaction.finalized"])
            self.assertEqual(events[1].content, "打开音乐")
            self.assertEqual(events[2].content, "已经打开音乐")
            await application.close()

    async def test_qwen_direct_reply_writes_one_completed_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            await conversation.start()
            voice = InteractionService(
                StateEvents(lambda _event: None), None, AsyncMock(),
                conversation=conversation)

            await voice._conversation_event("user_final", {"transcript": "你还记得我吗"})
            await voice._conversation_event("response_created", {"response_id": "r1"})
            await voice._conversation_event("assistant_final", {
                "response_id": "r1", "transcript": "当然记得。"})
            await voice._conversation_event("response_done", {
                "response_id": "r1", "cancelled": False, "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            self.assertEqual([item.type for item in events], [
                "interaction.started", "message.final", "message.final",
                "interaction.finalized"])
            self.assertEqual([item.content for item in events if item.type == "message.final"],
                             ["你还记得我吗", "当然记得。"])
            self.assertEqual(events[-1].status, "succeeded")

    async def test_qwen_real_event_order_binds_early_response_to_user_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            await conversation.start()
            voice = InteractionService(
                StateEvents(lambda _event: None), None, AsyncMock(),
                conversation=conversation)

            # DashScope 实际可能先创建 Response，之后才完成用户转写。
            await voice._conversation_event("response_created", {"response_id": "r1"})
            await voice._conversation_event("user_final", {"transcript": "你还记得我吗"})
            await voice._conversation_event("assistant_final", {
                "response_id": "r1", "transcript": "当然记得。"})
            await voice._conversation_event("response_done", {
                "response_id": "r1", "cancelled": False, "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            self.assertEqual([item.type for item in events], [
                "interaction.started", "message.final", "message.final",
                "interaction.finalized"])
            self.assertEqual([item.content for item in events
                              if item.type == "message.final"],
                             ["你还记得我吗", "当然记得。"])
            self.assertEqual(events[-1].status, "succeeded")

    async def test_cancelled_early_response_is_not_bound_to_next_user_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            await conversation.start()
            voice = InteractionService(
                StateEvents(lambda _event: None), None, AsyncMock(),
                conversation=conversation)

            await voice._conversation_event("response_created", {"response_id": "old"})
            await voice._conversation_event("response_done", {
                "response_id": "old", "cancelled": True, "has_tool_calls": False})
            await voice._conversation_event("user_final", {"transcript": "新的问题"})
            await voice._conversation_event("assistant_final", {
                "response_id": "old", "transcript": "不应写入的旧回答"})
            await voice._conversation_event("response_created", {"response_id": "new"})
            await voice._conversation_event("assistant_final", {
                "response_id": "new", "transcript": "新的回答"})
            await voice._conversation_event("response_done", {
                "response_id": "new", "cancelled": False, "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            self.assertEqual([item.content for item in events
                              if item.type == "message.final"],
                             ["新的问题", "新的回答"])
            self.assertEqual([item.status for item in events
                              if item.type == "interaction.finalized"],
                             ["succeeded"])

    async def test_late_cancelled_response_cannot_close_new_voice_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            await conversation.start()
            voice = InteractionService(
                StateEvents(lambda _event: None), None, AsyncMock(),
                conversation=conversation)

            await voice._conversation_event("user_final", {"transcript": "第一句"})
            await voice._conversation_event("response_created", {"response_id": "old"})
            await voice._conversation_event("user_final", {"transcript": "第二句"})
            await voice._conversation_event("response_done", {
                "response_id": "old", "cancelled": True, "has_tool_calls": False})
            await voice._conversation_event("response_created", {"response_id": "new"})
            await voice._conversation_event("assistant_final", {
                "response_id": "new", "transcript": "第二句的回答"})
            await voice._conversation_event("response_done", {
                "response_id": "new", "cancelled": False, "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            terminals = [item for item in events if item.type == "interaction.finalized"]
            self.assertEqual([item.status for item in terminals],
                             ["interrupted", "succeeded"])
            self.assertEqual([item.content for item in events
                              if item.type == "message.final" and item.role == "assistant"],
                             ["第二句的回答"])

    async def test_qwen_task_reuses_interaction_and_codex_receives_prior_voice_context(self):
        with tempfile.TemporaryDirectory() as directory:
            captured = {}

            class CompleteExecutor:
                def __init__(self, task_id, **context):
                    captured["task_id"] = task_id
                    captured.update(context)

                async def run(self, goal, progress, approve):
                    return {"outcome": "completed", "summary": "已经开始播放",
                            "evidence": []}

                async def cancel(self):
                    pass

            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            application = BoxAgentApplication(
                lambda _event: None, CompleteExecutor, None,
                conversation_service=conversation)
            await application.start()
            voice = application.interaction_service

            await voice._conversation_event("user_final", {"transcript": "我今天有点累"})
            await voice._conversation_event("response_created", {"response_id": "r1"})
            await voice._conversation_event("assistant_final", {
                "response_id": "r1", "transcript": "那我们放松一点。"})
            await voice._conversation_event("response_done", {
                "response_id": "r1", "cancelled": False, "has_tool_calls": False})

            await voice._conversation_event("user_final", {"transcript": "打开音乐"})
            await voice._conversation_event("response_created", {"response_id": "r2"})
            await voice._conversation_event("assistant_final", {
                "response_id": "r2", "transcript": "好，我去播放。"})
            await voice._conversation_event("response_done", {
                "response_id": "r2", "cancelled": False, "has_tool_calls": True})
            result = await voice._handle_tool(
                "run_task", '{"goal":"Qwen 改写后的内容"}', {"response_id": "r2"})
            task_result = await application.job
            await voice._conversation_event("response_created", {"response_id": "r3"})
            await voice._conversation_event("assistant_final", {
                "response_id": "r3", "transcript": "音乐已经放好了。"})
            await voice._conversation_event("response_done", {
                "response_id": "r3", "cancelled": False, "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            user_messages = [item for item in events
                             if item.type == "message.final" and item.role == "user"]
            terminals = [item for item in events if item.type == "interaction.finalized"]
            self.assertEqual(result["status"], "accepted")
            self.assertEqual(task_result["status"], "succeeded")
            self.assertEqual([item.content for item in user_messages],
                             ["我今天有点累", "打开音乐"])
            self.assertEqual(len(terminals), 2)
            self.assertEqual(terminals[-1].task_id, captured["task_id"])
            self.assertIsNone(user_messages[-1].task_id)
            self.assertEqual([item.content for item in captured["prior_messages"]],
                             ["我今天有点累", "那我们放松一点。"])
            self.assertEqual(application.execution_service.last_result["goal"], "打开音乐")
            self.assertNotIn("音乐已经放好了。", [item.content for item in events])
            await application.close()

    async def test_tool_result_voice_response_cannot_complete_a_new_user_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(
                JsonlSessionStore(Path(directory) / "conversations"))
            await conversation.start()
            voice = InteractionService(
                StateEvents(lambda _event: None), None, AsyncMock(),
                conversation=conversation)

            await voice._conversation_event("user_final", {"transcript": "新的聊天"})
            await voice._conversation_event("response_created", {
                "response_id": "task-result", "origin": "tool_result"})
            await voice._conversation_event("assistant_final", {
                "response_id": "task-result", "transcript": "旧任务完成了"})
            await voice._conversation_event("response_done", {
                "response_id": "task-result", "cancelled": False,
                "has_tool_calls": False})
            await voice._conversation_event("response_created", {
                "response_id": "user-response", "origin": "user"})
            await voice._conversation_event("assistant_final", {
                "response_id": "user-response", "transcript": "继续聊吧"})
            await voice._conversation_event("response_done", {
                "response_id": "user-response", "cancelled": False,
                "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            self.assertEqual([item.content for item in events
                              if item.type == "message.final"],
                             ["新的聊天", "继续聊吧"])
            self.assertEqual([item.status for item in events
                              if item.type == "interaction.finalized"],
                             ["succeeded"])

    async def test_non_task_tool_result_completes_its_original_interaction(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(
                Path(directory) / "conversations"))
            await conversation.start()
            handled = []

            async def handle(name, arguments, *, interaction=None):
                handled.append((name, interaction.interaction_id))
                return {"status": "succeeded"}

            voice = InteractionService(
                StateEvents(lambda *_args: None), None, handle,
                conversation=conversation)
            await voice._conversation_event("user_final", {"transcript": "记住我喜欢爵士乐"})
            await voice._conversation_event("response_created", {
                "response_id": "tool-call-response", "origin": "user"})
            await voice._handle_tool(
                "remember_memory", {"content": "用户喜欢爵士乐"},
                {"response_id": "tool-call-response", "call_id": "call-1"})
            await voice._conversation_event("response_done", {
                "response_id": "tool-call-response", "cancelled": False,
                "has_tool_calls": True})
            await voice._conversation_event("response_created", {
                "response_id": "tool-result-response", "origin": "tool_result",
                "call_id": "call-1"})
            await voice._conversation_event("assistant_final", {
                "response_id": "tool-result-response", "transcript": "已经记住了"})
            await voice._conversation_event("response_done", {
                "response_id": "tool-result-response", "cancelled": False,
                "has_tool_calls": False})

            session = await conversation.active_session()
            events = await conversation.events(session.session_id)
            self.assertEqual([item.content for item in events
                              if item.type == "message.final"],
                             ["记住我喜欢爵士乐", "已经记住了"])
            self.assertEqual(len([item for item in events
                                  if item.type == "interaction.finalized"]), 1)
            self.assertEqual(handled[0][0], "remember_memory")


class ContextCheckpointCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_interaction_is_checkpointed_asynchronously(self):
        class Generator:
            provider = "deepseek"
            model = "deepseek-flash"

            def __init__(self):
                self.calls = []

            async def generate(self, *, previous, messages):
                self.calls.append((previous, messages))
                return {"summary": "用户喜欢爵士乐", "user_facts": ["用户喜欢爵士乐"],
                        "decisions": [], "outcomes": [], "open_loops": [],
                        "entities": [], "commitments": [],
                        "time_range": {"start": None, "end": None},
                        "salient_events": []}

        with tempfile.TemporaryDirectory() as directory:
            store = JsonlSessionStore(Path(directory) / "conversations")
            generator = Generator()
            coordinator = ContextCheckpointCoordinator(
                store, generator, trigger_characters=8,
                source_character_limit=100)
            service = ConversationService(store, checkpoint_sink=coordinator)
            await coordinator.start()
            context = await service.begin_interaction("我喜欢爵士乐")
            await service.finish_interaction(
                context, status="succeeded", assistant_content="记住了")
            self.assertEqual(len(generator.calls), 0)

            await coordinator.drain()
            checkpoint, tail = await service.runtime_context(
                context.session.session_id, "qwen_realtime", limit=None)
            segments = await store.context_segments(
                context.session.session_id, "qwen_realtime")
            events = await service.events(context.session.session_id)

            self.assertEqual(checkpoint.content["summary"], "用户喜欢爵士乐")
            self.assertEqual(tail, [])
            self.assertEqual(segments[0].end_sequence,
                             checkpoint.covered_through_sequence)
            self.assertEqual(len(checkpoint.source_hash), 64)
            self.assertIn("context.checkpoint.created",
                          [item.type for item in events])
            await coordinator.close()


class RuntimeContextProjectorTests(unittest.TestCase):
    def test_product_messages_are_projected_as_native_runtime_history(self):
        turns = [
            ProductEvent(1, "evt-1", "message.final", "ses_test", 1.0,
                         role="user", content="我偏好简洁回答", source="text"),
            ProductEvent(2, "evt-2", "message.final", "ses_test", 2.0,
                         role="assistant", content="好的", runtime="qwen_realtime"),
        ]
        result = RuntimeContextProjector().runtime_messages(turns=turns)
        self.assertEqual([(item.role, item.content) for item in result], [
            ("user", "我偏好简洁回答"), ("assistant", "好的")])
        self.assertEqual([item.sequence for item in result], [1, 2])

    def test_recent_turn_budget_drops_older_turns(self):
        turns = [
            ProductEvent(1, "1", "message.final", "ses_test", 1.0,
                         role="user", content="旧" * 10),
            ProductEvent(2, "2", "message.final", "ses_test", 2.0,
                         role="assistant", content="新" * 10),
        ]
        result = RuntimeContextProjector().runtime_messages(
            turns=turns, character_budget=10)
        self.assertEqual(list(result), [])

    def test_recent_turn_budget_starts_at_a_complete_user_turn(self):
        turns = [
            ProductEvent(1, "1", "message.final", "ses_test", 1.0,
                         role="user", content="很长的旧问题"),
            ProductEvent(2, "2", "message.final", "ses_test", 2.0,
                         role="assistant", content="旧回答"),
            ProductEvent(3, "3", "message.final", "ses_test", 3.0,
                         role="user", content="新的问题"),
            ProductEvent(4, "4", "message.final", "ses_test", 4.0,
                         role="assistant", content="新的回答"),
        ]
        result = RuntimeContextProjector().runtime_messages(
            turns=turns, character_budget=12)
        self.assertEqual([(item.role, item.content) for item in result], [
            ("user", "新的问题"), ("assistant", "新的回答")])

    def test_warm_runtime_request_keeps_history_out_of_current_goal(self):
        turn = ProductEvent(1, "1", "message.final", "ses_test", 1.0,
                            role="user", content="旧请求")
        request = RuntimeRequestBuilder().prepare(HarnessInput(
            goal="当前请求", turns=[turn]))
        self.assertEqual(request.query, "当前请求")
        self.assertEqual([item.content for item in request.history], ["旧请求"])

    def test_history_delta_contains_only_messages_after_runtime_cursor(self):
        turns = [
            ProductEvent(2, "1", "message.final", "ses_test", 1.0,
                         role="user", content="Codex 已看过"),
            ProductEvent(5, "2", "message.final", "ses_test", 2.0,
                         role="assistant", content="Qwen 后来回答"),
        ]
        request = RuntimeRequestBuilder().prepare(HarnessInput(
            goal="当前请求", turns=turns, context_cursor=3))
        self.assertEqual([item.content for item in request.history_delta],
                         ["Qwen 后来回答"])

    def test_memory_evidence_is_fenced_separately_from_native_history(self):
        request = RuntimeRequestBuilder().prepare(HarnessInput(
            goal="当前请求", memories=[{"content": "用户偏好中文"}]))
        self.assertEqual(request.history, ())
        self.assertIn("长期记忆证据", request.evidence_context)
        self.assertIn("用户偏好中文", request.evidence_context)

    def test_qwen_restore_history_is_bounded_native_messages(self):
        turns = [
            ProductEvent(1, "1", "message.final", "ses_test", 1.0,
                         role="user", content="旧" * 20),
            ProductEvent(2, "2", "message.final", "ses_test", 2.0,
                         role="assistant", content="最近回答"),
        ]
        result = RuntimeContextProjector().runtime_messages(
            turns=turns, character_budget=8)
        self.assertEqual(list(result), [])

    def test_qwen_restore_keeps_checkpoint_separate_from_native_messages(self):
        checkpoint = ContextCheckpoint(
            checkpoint_id="ckp_test", session_id="ses_test",
            runtime="qwen_realtime", source_from_sequence=1,
            covered_through_sequence=4, source_hash="abc", created_at=3.0,
            provider="deepseek", model="deepseek-flash",
            content={"summary": "用户喜欢爵士乐", "user_facts": [],
                     "decisions": [], "open_loops": []})
        turn = ProductEvent(5, "evt-5", "message.final", "ses_test", 4.0,
                            role="user", content="那摇滚呢")

        restored = RuntimeContextProjector().restore_context(
            checkpoint=checkpoint, turns=[turn])

        self.assertIn("boxagent_context_checkpoint", restored.checkpoint)
        self.assertIn("用户喜欢爵士乐", restored.checkpoint)
        self.assertEqual([item.content for item in restored.messages], ["那摇滚呢"])

    def test_runtime_history_redacts_credentials_before_replay(self):
        turn = ProductEvent(
            1, "evt-secret", "message.final", "ses_test", 1.0,
            role="user", content="密码: example-only-not-real")

        result = RuntimeContextProjector().runtime_messages(turns=[turn])

        self.assertNotIn("example-only-not-real", result[0].content)
        self.assertIn("敏感信息已删除", result[0].content)


class HarnessTests(unittest.TestCase):
    def test_persona_is_compiled_into_developer_instructions_not_user_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SOUL.md"
            path.write_text("叫用户队长，回答要简洁。", encoding="utf-8")
            request = RuntimeRequestBuilder(persona=load_persona(path)).prepare("打开音乐")

        self.assertEqual(request.query, "打开音乐")
        self.assertIn("叫用户队长", request.developer_instructions)
        self.assertIn("不能修改工具权限", request.developer_instructions)
        self.assertEqual(request.persona_source, str(path.resolve()))

    def test_persona_name_overrides_product_name_for_self_identity(self):
        path = Path("SOUL.md")
        request = RuntimeRequestBuilder(persona=Persona(
            content="名字：reze\n\n保持自然简洁。", source=path, name="reze"
        )).prepare("你是谁？")

        self.assertIn("当前角色名称是“reze”", request.developer_instructions)
        self.assertIn("不要自称产品名、系统名或模型名", request.developer_instructions)

    def test_default_soul_file_is_available(self):
        from boxagent.bootstrap.settings import load_settings

        settings = load_settings()
        self.assertTrue(settings.soul_file.is_file())
