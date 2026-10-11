import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from boxagent.application.turn_preparation import TurnPreparer
from boxagent.domain.context.models import project_context
from boxagent.infrastructure.context.qwen_ledger import QwenContextRecorder
from boxagent.infrastructure.persistence.jsonl_context_ledger import JsonlContextLedgerStore
from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession


class Memory:
    async def context_packet(self, query, **kwargs):
        return "记忆：用户喜欢乌龙茶"


class TurnPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_control_does_not_infer_tasks_from_companion_prose(self):
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
        self.assertEqual([e["type"] for e in session.ws.sent], ["conversation.item.create"])

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

    async def test_short_confirmation_does_not_guess_intent(self):
        class Intent:
            async def estimate(self, query):
                raise AssertionError("should not estimate")
        preparer = TurnPreparer(Memory(), intent=Intent())
        packet, report = await preparer.prepare("继续", session_id="s", query_id="q")
        self.assertEqual(report["jev"]["status"], "ambiguous")
        self.assertIn("乌龙茶", packet)
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
