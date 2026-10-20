import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.turn_preparation import TurnPreparer
from boxagent.core.events import StateEvents
from boxagent.domain.conversation import ConversationService
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.context.models import project_context
from boxagent.infrastructure.context.qwen_ledger import QwenContextRecorder
from boxagent.infrastructure.persistence.jsonl_context_ledger import JsonlContextLedgerStore
from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession
from boxagent.domain.interaction.delegation import DecisionAdvice


class Memory:
    async def context_packet(self, query, **kwargs):
        return "记忆：用户喜欢乌龙茶"


class TurnPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_decision_is_installed_but_late_optional_memory_is_dropped(self):
        class SlowMemory:
            async def context_packet(self, query, **kwargs):
                await asyncio.sleep(.04)
                return "late memory must not enter this turn"
        class SlowIntent:
            async def estimate(self, query, *, context=""):
                await asyncio.sleep(.45)
                return {"p_action": .98, "p_status": .01, "p_conversation": .01}
        preparer = TurnPreparer(SlowMemory(), intent=SlowIntent(), budget=.02)
        try:
            start = time.perf_counter()
            packet, report = await preparer.prepare("打开计算器", session_id="s", query_id="q")
            self.assertGreaterEqual(time.perf_counter() - start, .4)
            self.assertEqual(report["jev"]["status"], "ready")
            self.assertEqual(report["memory"]["status"], "timeout")
            self.assertIn('"p_action":0.98', packet)
            self.assertNotIn("late memory", packet)
        finally:
            await preparer.close()

    async def test_decision_outage_is_bounded_and_cancels_its_request(self):
        cancelled = asyncio.Event()
        class HungIntent:
            async def estimate(self, query, *, context=""):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
        preparer = TurnPreparer(Memory(), intent=HungIntent(), decision_timeout=.03)
        try:
            packet, report = await asyncio.wait_for(preparer.prepare("？", session_id="s", query_id="q"), .3)
            self.assertEqual(report["jev"]["status"], "timeout")
            self.assertNotIn("p_action", packet)
            self.assertTrue(cancelled.is_set())
        finally:
            await preparer.close()

    async def test_review_interrupted_by_new_query_cannot_dispatch_old_task(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, release = asyncio.Event(), asyncio.Event()
            class Intent:
                async def review_delegation(self, query, reply, *, context=""):
                    entered.set()
                    await release.wait()
                    return {"p_delegation": .99}
            preparer = TurnPreparer(Memory(), intent=Intent())
            app = BoxAgentApplication(lambda _: None, AsyncMock(), None,
                conversation_service=ConversationService(JsonlSessionStore(Path(directory))))
            service = app.interaction_service
            try:
                await app.start()
                await service._conversation_event("user_final", {"transcript": "打开计算器"})
                old = service.interaction
                service.response_interactions["old"] = old
                service.decisions[old.interaction_id] = DecisionAdvice(.98)
                service.turn_preparer = preparer
                review = asyncio.create_task(service._review_delegation("old", "我现在开始操作"))
                await entered.wait()
                service.turn_preparer = None
                await service._conversation_event("user_final", {"transcript": "先不要，聊聊吧"})
                release.set()
                self.assertFalse(await review)
                self.assertIsNone(app.job)
            finally:
                await preparer.close()
                await app.close()

    async def test_semantic_review_requires_valid_explicit_approval(self):
        for value in ({"p_delegation": .79}, {"p_delegation": .8},
                      {"p_delegation": True}, {"p_delegation": float("nan")}, {}):
            with self.subTest(value=value):
                intent = AsyncMock()
                intent.review_delegation.return_value = value
                preparer = TurnPreparer(Memory(), intent=intent)
                approved = await preparer.review_delegation("打开计算器", "我现在开始")
                self.assertEqual(approved, value == {"p_delegation": .8})

    def test_decision_disagreements_and_short_confirmations_remain_qwen_owned(self):
        for advice, expected in ((DecisionAdvice(.01, .99, 0), "status"),
                                 (DecisionAdvice(.01, 0, .99), "conversation"),
                                 (DecisionAdvice(.7, 0, .95), None),
                                 (DecisionAdvice(.1, .5, .5), None),
                                 (DecisionAdvice(.01), None)):
            with self.subTest(advice=advice):
                self.assertEqual(advice.exclusion, expected)

    async def test_recorded_actual_call_for_chat_is_rejected_and_followup_stays_bound(self):
        case = json.loads((Path(__file__).parent / "fixtures/voice_task_clarification.json").read_text())
        for query in (case["user_query"], "你现在在干嘛呢？"):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as directory:
                factory = AsyncMock()
                app = BoxAgentApplication(
                    lambda _: None, factory, None,
                    conversation_service=ConversationService(JsonlSessionStore(Path(directory))))
                voice = QwenRealtimeSession(app.interaction_service._handle_tool, app.events.emit,
                                            host_turn_control=True,
                                            conversation_event=app.interaction_service._conversation_event)

                class Socket:
                    count = 0
                    async def send(self, raw):
                        if json.loads(raw)["type"] == "response.create":
                            self.count += 1
                            await voice.receive({"type": "response.created", "response": {
                                "id": f"reply-{self.count}"}})

                voice.ws = Socket()
                try:
                    await app.start()
                    interaction = app.interaction_service
                    await interaction._conversation_event("user_final", {"transcript": query})
                    context = interaction.interaction
                    interaction.decisions[context.interaction_id] = DecisionAdvice(.01, .99, 0)
                    voice.begin_query(context.interaction_id)
                    await voice.respond_to_query("", query_id=context.interaction_id)
                    await voice.receive({"type": "response.audio_transcript.done", "response_id": "reply-1",
                                         "transcript": case["observed_reply"]})
                    await voice.receive({"type": "response.function_call_arguments.done", "response_id": "reply-1",
                                         "call_id": "recorded-call", "name": "run_task", "arguments": "{}"})
                    await asyncio.gather(*tuple(voice.deliveries))
                    call_id, name, result = await voice.results.get()
                    self.assertEqual(result["status"], "not_delegated")
                    self.assertIs(interaction.interaction, context)
                    self.assertEqual(app.state.task, "idle")
                    self.assertIsNone(app.job)
                    self.assertEqual(len(app.execution_service.pending), 0)
                    factory.assert_not_called()
                    await voice._finalize_response("reply-1", source="provider")
                    await voice.results.put((call_id, name, result))
                    voice.last_user_activity = 0
                    worker = asyncio.create_task(voice.deliver_results())
                    try:
                        async with asyncio.timeout(.5):
                            while "reply-2" not in voice.response_context:
                                await asyncio.sleep(.001)
                        reply = "你想让我做什么？" if query == case["user_query"] else "我现在没有在执行任务。"
                        await voice.receive({"type": "response.audio_transcript.done", "response_id": "reply-2",
                                             "transcript": reply})
                        await voice._finalize_response("reply-2", source="provider")
                    finally:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)
                    events = await app.conversation_service.events(context.session.session_id)
                    terminals = [e for e in events if e.type == "interaction.finalized"]
                    self.assertEqual([e.status for e in terminals], ["succeeded"])
                    self.assertTrue(all(e.task_id is None for e in terminals))
                    self.assertIn(reply, [e.content for e in events if e.type == "message.final"])
                    self.assertIsNone(interaction.interaction)
                    self.assertFalse(any(e.type.startswith("task.") for e in events))
                finally:
                    await app.close()

    async def test_excluded_implicit_delegation_settles_chat_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            factory = AsyncMock()
            app = BoxAgentApplication(lambda _: None, factory, None,
                conversation_service=ConversationService(JsonlSessionStore(Path(directory))))
            try:
                await app.start()
                interaction = app.interaction_service
                await interaction._conversation_event("user_final", {"transcript": "？"})
                context = interaction.interaction
                voice = QwenRealtimeSession(interaction._handle_tool, app.events.emit, host_turn_control=True,
                                            conversation_event=interaction._conversation_event)
                voice.current_query_id = context.interaction_id
                voice.response_context["reply"] = {"kind": "user", "query_id": context.interaction_id}
                interaction.response_interactions["reply"] = context
                voice.response_transcripts["reply"] = "我这就帮你打开计算器。"
                await voice._finalize_response("reply", source="provider")
                await asyncio.gather(*tuple(voice.deliveries))
                events = await app.conversation_service.events(context.session.session_id)
                self.assertEqual([e.status for e in events if e.type == "interaction.finalized"], ["succeeded"])
                factory.assert_not_called()
                self.assertEqual(app.state.error, "")
                self.assertIsNone(app.job)
            finally:
                await app.close()

    async def test_explicit_tool_call_repairs_recorded_close_calculator_false_negative(self):
        """A low first-pass score must not veto a semantically verified operation."""
        with tempfile.TemporaryDirectory() as directory:
            running = asyncio.Event()
            finish = asyncio.Event()
            goals = []

            class Executor:
                async def run(self, goal, progress, approve):
                    goals.append(goal)
                    running.set()
                    await finish.wait()
                    return {"outcome": "succeeded", "summary": "计算器已关闭"}

                async def cancel(self):
                    finish.set()

            intent = AsyncMock()
            intent.review_delegation.return_value = {"p_delegation": .99}
            preparer = TurnPreparer(Memory(), intent=intent)
            app = BoxAgentApplication(
                lambda _: None, lambda *_args, **_kwargs: Executor(), None,
                conversation_service=ConversationService(JsonlSessionStore(Path(directory))),
                turn_preparer=preparer)
            interaction = app.interaction_service
            voice = QwenRealtimeSession(
                interaction._handle_tool, app.events.emit, host_turn_control=True,
                conversation_event=interaction._conversation_event,
                delegation_review=interaction._review_delegation)
            try:
                await app.start()
                await interaction._conversation_event(
                    "user_final", {"transcript": "关闭计算器"})
                context = interaction.interaction
                interaction.decisions[context.interaction_id] = DecisionAdvice(
                    .12, .37, .96)
                voice.current_query_id = context.interaction_id
                voice.response_context["reply"] = {
                    "kind": "user", "query_id": context.interaction_id}
                interaction.response_interactions["reply"] = context
                voice.response_transcripts["reply"] = "这就去把计算器关了。"
                voice._dispatch_tool_call({
                    "response_id": "reply", "call_id": "close-calculator",
                    "name": "run_task", "arguments": "{}"})
                await asyncio.wait_for(running.wait(), .5)
                self.assertEqual(goals, ["关闭计算器"])
                self.assertEqual(app.state.task, "running")
                intent.review_delegation.assert_awaited_once()
            finally:
                finish.set()
                await app.close()
                await preparer.close()

    async def test_live_progress_reply_does_not_queue_chat_as_a_task(self):
        """FIX-03: replay the live failure through real Product/Execution services."""
        case = json.loads((Path(__file__).parent / "fixtures/voice_task_progress.json").read_text())
        status_case = case["recorded_status_case"]
        samples = [(case["user_query"], reply)
                   for reply in (case["observed_reply"], *case["progress_variants"])]
        samples.extend((status_case["user_query"], reply)
                       for reply in (status_case["observed_reply"], *status_case["variants"]))
        for source in ("provider", "audio_done"):
            for user_query, reply in samples:
                with self.subTest(source=source, reply=reply), tempfile.TemporaryDirectory() as directory:
                    running = asyncio.Event()
                    finish = asyncio.Event()
                    goals = []

                    class Executor:
                        async def run(self, goal, progress, approve):
                            goals.append(goal)
                            running.set()
                            await finish.wait()
                            return {"outcome": "blocked", "summary": "测试执行器未操作电脑"}

                        async def cancel(self):
                            finish.set()

                    app = BoxAgentApplication(
                        lambda _: None, lambda *_args, **_kwargs: Executor(), None,
                        conversation_service=ConversationService(JsonlSessionStore(Path(directory))))
                    interaction = app.interaction_service
                    voice = QwenRealtimeSession(
                        interaction._handle_tool, app.events.emit, host_turn_control=True,
                        conversation_event=interaction._conversation_event,
                        delegation_review=interaction._review_delegation)

                    class Socket:
                        response_count = 0

                        async def send(self, raw):
                            if json.loads(raw)["type"] == "response.create":
                                self.response_count += 1
                                await voice.receive({"type": "response.created", "response": {
                                    "id": f"reply-{self.response_count}"}})

                    voice.ws = Socket()
                    try:
                        await app.start()
                        await interaction._conversation_event("user_final", {
                            "transcript": case["active_goal"]})
                        task_context = interaction.interaction
                        voice.begin_query(task_context.interaction_id)
                        await voice.respond_to_query("", query_id=task_context.interaction_id)
                        await voice.receive({"type": "response.function_call_arguments.done",
                                             "response_id": "reply-1", "call_id": "original-task",
                                             "name": "run_task", "arguments": "{}"})
                        await asyncio.gather(*tuple(voice.deliveries))
                        await asyncio.wait_for(running.wait(), .5)
                        await voice.receive({"type": "response.done", "response": {"id": "reply-1"}})
                        original_job = app.job
                        original_task_id = app.state.task_id

                        await interaction._conversation_event("user_final", {
                            "transcript": user_query})
                        chat_context = interaction.interaction
                        interaction.decisions[chat_context.interaction_id] = DecisionAdvice(.01, .99, 0)
                        voice.begin_query(chat_context.interaction_id)
                        await voice.respond_to_query("", query_id=chat_context.interaction_id)
                        await voice.receive({"type": "response.audio_transcript.done",
                                             "response_id": "reply-2", "transcript": reply})
                        await voice._finalize_response("reply-2", source=source)
                        await asyncio.gather(*tuple(voice.deliveries))

                        self.assertEqual(goals, [case["active_goal"]])
                        self.assertEqual(len(app.execution_service.pending), case["expected"]["new_task_count"])
                        self.assertIs(app.job, original_job)
                        self.assertFalse(original_job.done())
                        self.assertEqual(app.state.task_id, original_task_id)
                        self.assertFalse(voice.input_turn_pending)
                        events = await app.conversation_service.events(chat_context.session.session_id)
                        chat_terminal = [e for e in events if e.type == "interaction.finalized"
                                         and e.interaction_id == chat_context.interaction_id]
                        self.assertEqual([e.status for e in chat_terminal], ["succeeded"])
                        self.assertTrue(all(e.task_id is None for e in chat_terminal))
                        self.assertFalse(any(e.type == "interaction.finalized"
                                             and e.interaction_id == task_context.interaction_id for e in events))
                    finally:
                        finish.set()
                        await app.close()

    async def test_pending_response_is_bound_before_the_next_request(self):
        session = QwenRealtimeSession(None, lambda *_args, **_kw: None, host_turn_control=True)
        class Socket:
            def __init__(self):
                self.sent = []
            async def send(self, raw):
                self.sent.append(json.loads(raw))
            async def close(self):
                pass
        session.ws = Socket()
        session.begin_query("speed")
        first = asyncio.create_task(session._create_response({"kind": "user", "query_id": "speed"}))
        await asyncio.sleep(0)
        first.cancel()  # User starts another utterance before the provider ack.
        await asyncio.gather(first, return_exceptions=True)
        session.begin_query("model-question")
        second = asyncio.create_task(session._create_response({"kind": "user", "query_id": "model-question"}))
        await asyncio.sleep(.01)
        self.assertEqual(sum(row["type"] == "response.create" for row in session.ws.sent), 1)
        await session.receive({"type": "response.created", "response": {"id": "late-speed"}})
        await asyncio.sleep(.01)
        self.assertIn("late-speed", session.cancelled_responses)
        self.assertEqual(sum(row["type"] == "response.create" for row in session.ws.sent), 2)
        await session.receive({"type": "response.created", "response": {"id": "model-reply"}})
        await second
        self.assertEqual(session.response_context["model-reply"]["query_id"], "model-question")
        await session._finalize_response("late-speed", source="provider")
        self.assertTrue(session.input_turn_pending)

    async def test_notification_waits_for_new_query_and_promise_dispatches_original_goal(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            await conversation.start()
            handled = []
            async def tool(name, arguments, *, interaction=None):
                handled.append((name, interaction.user_event.content))
                return {"status": "accepted", "task_id": "science"}
            interaction = InteractionService(StateEvents(lambda *_: None), None, tool,
                                             conversation=conversation)
            session = QwenRealtimeSession(interaction._handle_tool, lambda *_args, **_kw: None,
                                          host_turn_control=True,
                                          conversation_event=interaction._conversation_event,
                                          delegation_review=AsyncMock(return_value=True))
            class Socket:
                def __init__(self):
                    self.sent = []
                async def send(self, raw):
                    row = json.loads(raw)
                    self.sent.append(row)
                    if row["type"] == "response.create":
                        await session.receive({"type": "response.created", "response": {
                            "id": "response-" + str(len(self.sent))}})
                async def close(self):
                    pass
            session.ws = Socket()
            await interaction._conversation_event("user_final", {
                "transcript": "打开科学计算器，计算二的五次方加根号四十二"})
            query_id = interaction.interaction.interaction_id
            session.begin_query(query_id)
            await session.notifications.put({"notification_id": "old-failure", "outcome": "failed",
                                             "summary": "旧任务无法解析"})
            notices = asyncio.create_task(session.deliver_notifications())
            await asyncio.sleep(.02)
            self.assertEqual(session.ws.sent, [])
            # A waiting notification must not retain the delivery lock.
            await asyncio.wait_for(session.respond_to_query("", query_id=query_id), .2)
            response_id = session.response_id
            await session.receive({"type": "response.audio_transcript.done", "response_id": response_id,
                                   "transcript": "我这就去打开科学计算器，把 2 的 5 次方加上根号 42 算出来。"})
            await session.receive({"type": "response.done", "response": {"id": response_id}})
            await asyncio.gather(*tuple(session.deliveries))
            self.assertEqual(handled, [("run_task", "打开科学计算器，计算二的五次方加根号四十二")])
            await asyncio.sleep(.12)
            self.assertTrue(any("旧任务无法解析" in json.dumps(row, ensure_ascii=False)
                                for row in session.ws.sent))
            notices.cancel()
            await asyncio.gather(notices, return_exceptions=True)
            events = await conversation.events((await conversation.active_session()).session_id)
            self.assertFalse(any(row.type == "interaction.finalized" and row.status == "succeeded"
                                 for row in events))

    async def test_stale_response_cannot_bind_to_a_new_interaction_or_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            conversation = ConversationService(JsonlSessionStore(Path(directory)))
            await conversation.start()
            tool = AsyncMock()
            interaction = InteractionService(StateEvents(lambda *_: None), None, tool, conversation=conversation)
            await interaction._conversation_event("user_final", {"transcript": "快吗"})
            old_id = interaction.interaction.interaction_id
            await interaction._conversation_event("user_final", {"transcript": "打开科学计算器"})
            await interaction._conversation_event("response_created", {
                "response_id": "late", "origin": "user", "query_id": old_id})
            result = await interaction._handle_tool("run_task", "{}", {"response_id": "late"})
            self.assertEqual(result["status"], "failed")
            tool.assert_not_awaited()
            self.assertEqual(interaction.interaction.user_event.content, "打开科学计算器")

    async def test_host_control_does_not_dispatch_an_unbound_promise(self):
        calls = []
        async def tool(*args):
            calls.append(args)
            return {"status": "accepted"}
        session = QwenRealtimeSession(tool, lambda *_args, **_kw: None, host_turn_control=True)
        session.response_context["reply"] = {"kind": "user"}
        session.response_transcripts["reply"] = "好，我这就帮你播放轻音乐。"
        await session._finalize_response("reply", source="provider")
        await asyncio.sleep(0)
        self.assertEqual(calls, [])

    async def test_missing_delete_ack_stops_connection_before_new_query(self):
        session = QwenRealtimeSession(lambda *_: None, lambda *_args, **_kw: None,
                                     host_turn_control=True)
        session.reference_item = "old-reference"
        session.reference_query = "old-query"
        sent = []
        class Socket:
            async def send(self, raw):
                sent.append(json.loads(raw))
            async def close(self):
                pass
        session.ws = Socket()
        with self.assertRaises(TimeoutError):
            await session.respond_to_query("new-reference", query_id="new-query")
        self.assertTrue(session.closed)
        self.assertEqual([row["type"] for row in sent], ["conversation.item.delete"])


    async def test_stale_tool_continuation_does_not_answer_new_query(self):
        session = QwenRealtimeSession(lambda *_: None, lambda *_args, **_kw: None, host_turn_control=True)
        session.current_query_id = "new"
        session.response_context["old-response"] = {"query_id": "old"}
        session.response_calls["old-response"] = {"call"}
        session.call_queries["call"] = "old"
        class Socket:
            sent = []
            async def send(self, raw):
                self.sent.append(json.loads(raw))
        session.ws = Socket()
        await session.results.put(("call", "recall_memory", {"status": "succeeded"}))
        delivery = asyncio.create_task(session.deliver_results())
        await asyncio.sleep(.01)
        delivery.cancel()
        await asyncio.gather(delivery, return_exceptions=True)
        self.assertEqual(session.ws.sent, [])

    async def test_deadline_drops_late_result_and_does_not_queue_another(self):
        ready = asyncio.Event()
        class Slow:
            async def context_packet(self, query, **kwargs):
                await ready.wait()
                return "late-" + query
        preparer = TurnPreparer(Slow(), environment=lambda: "现在是周一", budget=.02)
        try:
            started = time.perf_counter()
            first, report = await preparer.prepare("第一个请求", session_id="s", query_id="q1")
            self.assertLess(time.perf_counter() - started, .15)
            self.assertEqual(report["memory"]["status"], "timeout")
            self.assertNotIn("late", first)
            second, report = await preparer.prepare("第二个请求", session_id="s", query_id="q2")
            self.assertEqual(report["memory"]["status"], "busy")
            self.assertNotIn("late", second)
            ready.set()
            await asyncio.sleep(.01)
        finally:
            await preparer.close()

    async def test_every_short_query_receives_its_own_decision_probability(self):
        calls = []
        class Intent:
            async def estimate(self, query, *, context=""):
                calls.append(query)
                return {"p_action": .02 + len(calls) / 100}
        preparer = TurnPreparer(Memory(), intent=Intent())
        queries = ["？", "?", "继续", "可以", "好", "嗯", "行", "不要", "取消", "播放", "打开计算器"]
        try:
            for index, query in enumerate(queries, start=1):
                with self.subTest(query=query):
                    packet, report = await preparer.prepare(query, session_id="s", query_id=f"q{index}")
                    reference = json.loads(packet.split("\n")[2])
                    self.assertEqual(report["jev"]["status"], "ready")
                    self.assertAlmostEqual(reference["intent"]["p_action"], .02 + index / 100)
                    self.assertEqual(reference["query_id"], f"q{index}")
                    self.assertIn("乌龙茶", packet)
            self.assertEqual(calls, queries)
        finally:
            await preparer.close()

    async def test_text_and_voice_short_queries_both_install_current_decision_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            intent = AsyncMock()
            intent.estimate.side_effect = [{"p_action": .02}, {"p_action": .8}]
            preparer = TurnPreparer(Memory(), intent=intent)
            app = BoxAgentApplication(lambda _: None, AsyncMock(), None,
                conversation_service=ConversationService(JsonlSessionStore(Path(directory))),
                turn_preparer=preparer)
            service = app.interaction_service
            packets = []
            class Voice:
                def begin_query(self, query_id):
                    pass
                async def submit_text(self, text, *, reference, query_id):
                    packets.append(("text", text, reference, query_id))
                async def respond_to_query(self, reference, *, query_id):
                    packets.append(("voice", "继续", reference, query_id))
                async def stop(self):
                    pass
            try:
                await app.start()
                service.voice = Voice()
                service._connect = AsyncMock()
                await service.submit_text("？")
                await service._conversation_event("user_final", {"transcript": "继续"})
                self.assertEqual([call.args for call in intent.estimate.await_args_list], [("？",), ("继续",)])
                self.assertIn("？", intent.estimate.await_args_list[1].kwargs["context"])
                self.assertEqual([row[0] for row in packets], ["text", "voice"])
                for row, probability in zip(packets, (.02, .8)):
                    reference = json.loads(row[2].split("\n")[2])
                    self.assertEqual(reference["query_id"], row[3])
                    self.assertEqual(reference["intent"]["p_action"], probability)
                self.assertNotEqual(packets[0][3], packets[1][3])
            finally:
                await preparer.close()
                await app.close()

    async def test_busy_previous_decision_does_not_skip_the_new_query_or_reuse_probability(self):
        release = asyncio.Event()
        calls = []
        class Intent:
            async def estimate(self, query, *, context=""):
                calls.append(query)
                if query == "first":
                    await release.wait()
                    return {"p_action": .99}
                return {"p_action": .01}
        preparer = TurnPreparer(Memory(), intent=Intent(), budget=.02, decision_timeout=.02)
        try:
            first, first_report = await preparer.prepare("first", session_id="s", query_id="q1")
            self.assertEqual(first_report["jev"]["status"], "timeout")
            self.assertNotIn("p_action", first)
            second, second_report = await preparer.prepare("？", session_id="s", query_id="q2")
            self.assertEqual(calls, ["first", "？"])
            self.assertEqual(second_report["jev"]["status"], "ready")
            self.assertIn('"p_action":0.01', second)
            release.set()
            await asyncio.sleep(.01)
            self.assertNotIn("0.99", second)
        finally:
            await preparer.close()

    async def test_close_cancels_all_overlapping_decisions(self):
        finished = []
        class Intent:
            async def estimate(self, query, *, context=""):
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.append(query)
        preparer = TurnPreparer(Memory(), intent=Intent(), budget=.01, decision_timeout=.01)
        await preparer.prepare("？", session_id="s", query_id="q1")
        await preparer.prepare("继续", session_id="s", query_id="q2")
        await preparer.close()
        self.assertCountEqual(finished, ["？", "继续"])
        self.assertEqual(preparer.pending, set())

    async def test_failed_or_invalid_decision_is_explicit_without_inventing_probability(self):
        for value in ({}, {"p_action": True}, {"p_action": float("nan")}, {"p_action": 2}, "bad", None):
            with self.subTest(value=value):
                class Intent:
                    async def estimate(self, query, *, context=""):
                        return value
                preparer = TurnPreparer(Memory(), intent=Intent())
                try:
                    packet, report = await preparer.prepare("？", session_id="s", query_id="q")
                    self.assertEqual(report["jev"]["status"], "invalid_result")
                    self.assertIn('"status":"invalid_result"', packet)
                    self.assertNotIn("p_action", packet)
                finally:
                    await preparer.close()
        class Unavailable:
            async def estimate(self, query, *, context=""):
                raise RuntimeError("unavailable")
        preparer = TurnPreparer(Memory(), intent=Unavailable())
        try:
            packet, report = await preparer.prepare("？", session_id="s", query_id="q")
            self.assertEqual(report["jev"]["status"], "failed:RuntimeError")
            self.assertIn('"status":"failed:RuntimeError"', packet)
            self.assertNotIn("p_action", packet)
        finally:
            await preparer.close()

    async def test_provider_actual_id_delete_ack_and_request_surface(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonlContextLedgerStore(Path(directory))
            recorder = QwenContextRecorder(ledger)
            session = QwenRealtimeSession(lambda *_: None, lambda *_args, **_kw: None,
                host_turn_control=True, product_session_id="ses_test", trace=recorder.observe)
            class Socket:
                sent = []
                async def send(self, raw):
                    event = json.loads(raw)
                    self.sent.append(event)
                    if event["type"] == "conversation.item.create":
                        await session.receive({"type": "conversation.item.created",
                            "item": {**event["item"], "id": "actual-" + str(len(self.sent))}})
                    elif event["type"] == "conversation.item.delete":
                        await session.receive({"type": "conversation.item.deleted", "item_id": event["item_id"]})
                    elif event["type"] == "response.create":
                        await session.receive({"type": "response.created", "response": {
                            "id": "reply-" + str(len(self.sent))}})
                async def close(self):
                    pass
            session.ws = Socket()
            session.ready.set()
            await session.submit_text("第一轮", reference="<boxagent_query_reference>包一", query_id="q1")
            old_id = session.reference_item
            await session.submit_text("第二轮", reference="<boxagent_query_reference>包二", query_id="q2")
            self.assertTrue(any(e.get("item_id") == old_id for e in session.ws.sent if e["type"] == "conversation.item.delete"))
            snapshot = project_context("ses_test", ledger.read("ses_test"))
            self.assertEqual(snapshot["delete_ack_count"], 1)
            refs = [e for e in snapshot["entries"] if e["category"] == "reference"]
            self.assertEqual(len(refs), 1)
            self.assertIn("包二", refs[0]["preview"])
            self.assertEqual(len(snapshot["requests"]), 2)
            self.assertEqual(len(snapshot["requests"][1]["removed"]), 1)

    async def test_asr_preparation_does_not_block_receive_loop(self):
        received = asyncio.Event()
        async def record(kind, payload):
            await received.wait()
        session = QwenRealtimeSession(lambda *_: None, lambda *_args, **_kw: None,
            host_turn_control=True, conversation_event=record)
        await asyncio.wait_for(session.receive({
            "type": "conversation.item.input_audio_transcription.completed", "transcript": "你好"}), .1)
        self.assertFalse(session.preparation_task.done())
        received.set()
        await session.preparation_task
