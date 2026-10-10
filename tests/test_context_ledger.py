"""REQ-01: Session Context Ledger, recording and snapshot contract."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from boxagent.agent.harness.instructions import append_persona
from boxagent.agent.harness.persona import Persona
from boxagent.agent.runtime.models import RuntimeMessage
from boxagent.domain.context import (
    LedgerEvent,
    distribute_percent,
    estimate_tokens,
    project_context,
)
from boxagent.infrastructure.context.qwen_ledger import QwenContextRecorder
from boxagent.infrastructure.persistence.jsonl_context_ledger import (
    FILE_NAME,
    JsonlContextLedgerStore,
)
from boxagent.infrastructure.runtimes.qwen.realtime import (
    INSTRUCTIONS,
    TOOLS,
    QwenRealtimeSession,
)


SESSION = "ses_0123456789abcdef"


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class EstimateTests(unittest.TestCase):
    def test_cjk_counts_one_token_per_character_and_ascii_quarter(self):
        self.assertEqual(estimate_tokens("你好世界"), 4)
        self.assertEqual(estimate_tokens("abcdefgh"), 2)
        self.assertEqual(estimate_tokens("abcde"), 2)       # ceil(5 / 4)
        self.assertEqual(estimate_tokens("打开 music"), 2 + 2)  # 2 CJK + ceil(6/4)
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens({"a": 1}), 2)     # '{"a":1}' -> 7 chars

    def test_percent_distribution_always_sums_to_one_hundred(self):
        shares = distribute_percent([1, 1, 1])
        self.assertAlmostEqual(sum(shares), 100.0)
        self.assertEqual(distribute_percent([0, 0]), [0.0, 0.0])
        self.assertAlmostEqual(sum(distribute_percent([7, 13, 29, 51, 3])), 100.0)


class LedgerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = JsonlContextLedgerStore(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_append_writes_header_and_monotonic_sequence_inside_session_dir(self):
        self.store.append(SESSION, "turn/start", {"channel": "text"})
        self.store.append(SESSION, "user/message", {"content": "你好", "tokens": 2})
        path = Path(self.temporary.name) / "sessions" / SESSION / FILE_NAME
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[0])["type"], "ledger")
        self.assertEqual([event.seq for event in self.store.read(SESSION)], [1, 2])
        # Accounting keys such as ``tokens`` must survive secret redaction.
        self.assertEqual(self.store.read(SESSION)[1].data["tokens"], 2)

    def test_secret_values_are_redacted(self):
        self.store.append(SESSION, "user/message",
                          {"content": "key sk-abcdefghijklmnopqrstu"})
        self.assertNotIn("sk-abcdef", self.store.read(SESSION)[0].data["content"])

    def test_torn_tail_is_ignored_and_repaired_before_next_append(self):
        self.store.append(SESSION, "turn/start", {})
        path = self.store.path(SESSION)
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"type":"user/mess')
        self.assertEqual(len(self.store.read(SESSION)), 1)
        fresh = JsonlContextLedgerStore(Path(self.temporary.name))
        event = fresh.append(SESSION, "turn/end", {})
        self.assertEqual(event.seq, 2)
        self.assertEqual([item.type for item in fresh.read(SESSION)],
                         ["turn/start", "turn/end"])

    def test_mid_file_corruption_is_rejected(self):
        self.store.append(SESSION, "turn/start", {})
        self.store.append(SESSION, "turn/end", {})
        path = self.store.path(SESSION)
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[1] = "{broken"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.read(SESSION)

    def test_session_id_cannot_escape_the_sessions_directory(self):
        for bad in ("../etc", "ses_../../x", "", "ses_a/b"):
            with self.assertRaises(ValueError):
                self.store.path(bad)


def event(seq, kind, at, **data):
    return LedgerEvent(seq=seq, type=kind, time=at, data=data)


class ProjectionTests(unittest.TestCase):
    def test_rebuild_starts_new_generation_but_timeline_keeps_old_turns(self):
        events = [
            event(1, "context/rebuild", 0, model="m"),
            event(2, "system/message", 0, category="instructions", chars=10, tokens=10),
            event(3, "turn/start", 1, channel="text"),
            event(4, "user/message", 1, category="user", chars=2, tokens=2, content="你好"),
            event(5, "context/rebuild", 5, model="m"),
            event(6, "system/message", 5, category="instructions", chars=10, tokens=10),
            event(7, "user/message", 5, category="history", chars=2, tokens=2),
        ]
        snapshot = project_context(SESSION, events)
        self.assertEqual(snapshot["generation"], 2)
        self.assertEqual(snapshot["totals"]["tokens"], 12)
        self.assertEqual([item["seq"] for item in snapshot["entries"]], [6, 7])
        self.assertEqual(snapshot["turns"], [])
        self.assertEqual(snapshot["all_turn_count"], 1)

    def test_memory_appended_after_response_created_is_flagged_late(self):
        events = [
            event(1, "context/rebuild", 0),
            event(2, "turn/start", 10.0, channel="voice"),
            event(3, "system/message", 10.01, category="environment", chars=40, tokens=30),
            event(4, "response/created", 10.40),
            event(5, "user/message", 10.90, category="user", content="今天天气"),
            event(6, "system/message", 11.20, category="memory", chars=300, tokens=200),
            event(7, "turn/prepare", 11.21,
                  memory={"status": "injected", "ms": 312.0, "chars": 300}),
            event(8, "assistant/message", 11.50, category="assistant", tokens=8),
        ]
        turn = project_context(SESSION, events)["turns"][0]
        self.assertTrue(turn["memory"]["late"])
        self.assertEqual(turn["response_ms"], 400)
        self.assertEqual(turn["environment_count"], 1)

    def test_text_memory_injected_before_response_is_on_time(self):
        events = [
            event(1, "turn/start", 1.0, channel="text"),
            event(2, "system/message", 1.3, category="memory", tokens=50),
            event(3, "turn/prepare", 1.3, memory={"status": "injected", "ms": 300}),
            event(4, "user/message", 1.31, category="user", content="继续"),
            event(5, "response/created", 1.6),
        ]
        turn = project_context(SESSION, events)["turns"][0]
        self.assertFalse(turn["memory"]["late"])


class QwenWireRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = JsonlContextLedgerStore(Path(self.temporary.name))
        self.recorder = QwenContextRecorder(self.store, model="qwen-test")
        persona = Persona(content="说话温柔。", source=Path("SOUL.md"), name="小芽")
        self.voice = QwenRealtimeSession(
            lambda *_args: None, lambda *_args, **_kw: None,
            product_session_id=SESSION,
            instructions=append_persona(INSTRUCTIONS, persona),
            trace=self.recorder.observe,
            environment_context=lambda: "[BoxAgent可信运行环境]\n时区：Asia/Shanghai",
            stable_memory_context="<boxagent_user_profile>喜欢乌龙茶</boxagent_user_profile>",
            conversation_checkpoint="<boxagent_context_checkpoint>{}</boxagent_context_checkpoint>",
            conversation_history=(
                RuntimeMessage("user", "昨天的问题", sequence=1),
                RuntimeMessage("assistant", "昨天的回答", sequence=2)))
        self.voice.ws = FakeSocket()

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def categories(self):
        snapshot = project_context(SESSION, self.store.read(SESSION))
        return snapshot, {row["key"]: row for row in snapshot["composition"]}

    async def test_connect_and_text_turn_are_recorded_from_wire_in_order(self):
        await self.voice.configure_session({"voice": "test"})
        self.voice.ready.set()
        await self.voice.inject_memory_context(
            "<boxagent_memory_context>用户喜欢乌龙茶</boxagent_memory_context>")
        await self.voice.submit_text("推荐一杯茶")

        snapshot, rows = self.categories()
        order = [entry["category"] for entry in snapshot["entries"]]
        self.assertEqual(order, [
            "instructions", "persona", "tools", "profile", "checkpoint",
            "history", "history", "memory", "environment", "user"])
        self.assertEqual(rows["tools"]["count"], 1)
        self.assertEqual(rows["tools"]["chars"],
                         len(json.dumps(TOOLS, ensure_ascii=False, separators=(",", ":"))))
        self.assertEqual(rows["persona"]["count"], 1)
        self.assertIn("小芽", self.store.read(SESSION)[2].data["content"])
        self.assertAlmostEqual(sum(row["percent"] for row in snapshot["composition"]), 100.0)
        self.assertEqual(len(snapshot["turns"]), 1)
        self.assertEqual(snapshot["turns"][0]["user"], "推荐一杯茶")

    async def test_repeated_turns_expose_accumulating_environment_packets(self):
        await self.voice.configure_session({"voice": "test"})
        self.voice.ready.set()
        for text in ("一", "二", "三"):
            await self.voice.submit_text(text)
        snapshot, rows = self.categories()
        self.assertEqual(rows["environment"]["count"], 3)
        self.assertEqual(snapshot["accumulation"]["environment"], 3)
        self.assertEqual(len(snapshot["turns"]), 3)

    async def test_voice_turn_and_provider_outputs(self):
        await self.voice.configure_session({"voice": "test"})
        self.voice.ready.set()
        self.voice.audio = type("Audio", (), {"clear": lambda self: None})()
        await self.voice.receive({"type": "input_audio_buffer.speech_started"})
        await self.voice.receive({"type": "response.created",
                                  "response": {"id": "r1"}})
        await self.voice.receive({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "现在几点", "item_id": "i1"})
        await self.voice.receive({"type": "response.audio_transcript.done",
                                  "response_id": "r1", "transcript": "下午三点。"})
        snapshot, rows = self.categories()
        self.assertEqual(snapshot["turns"][0]["channel"], "voice")
        self.assertEqual(snapshot["turns"][0]["user"], "现在几点")
        self.assertEqual(rows["environment"]["count"], 1)
        self.assertEqual(rows["assistant"]["count"], 1)
        self.assertIsNotNone(snapshot["turns"][0]["response_ms"])

    async def test_reconnect_resets_current_composition(self):
        await self.voice.configure_session({"voice": "test"})
        self.voice.ready.set()
        await self.voice.submit_text("第一轮")
        await self.voice.configure_session({"voice": "test"})
        snapshot, rows = self.categories()
        self.assertEqual(snapshot["generation"], 2)
        self.assertEqual(rows["environment"]["count"], 0)
        self.assertEqual(rows["user"]["count"], 0)

    async def test_ledger_failures_never_reach_the_conversation(self):
        class BrokenStore:
            def append(self, *_args, **_kwargs):
                raise OSError("disk full")

        for background in (False, True):
            await self._assert_fail_open(BrokenStore(), background)

    async def test_background_writer_preserves_order_and_observation_time(self):
        recorder = QwenContextRecorder(self.store, background=True,
                                       clock=iter([10.0, 11.0, 12.0]).__next__)
        recorder.turn_started(SESSION, channel="text")
        recorder.turn_prepared(SESSION, memory={"status": "empty"})
        recorder.observe("response.created", product_session_id=SESSION)
        self.assertTrue(recorder.flush())
        events = self.store.read(SESSION)
        self.assertEqual([item.type for item in events],
                         ["turn/start", "turn/prepare", "response/created"])
        self.assertEqual([item.time for item in events], [10.0, 11.0, 12.0])

    async def _assert_fail_open(self, broken, background):
        self.voice.ws = FakeSocket()

        failures = []
        recorder = QwenContextRecorder(
            broken, background=background,
            log=lambda event, **data: failures.append(event))
        self.voice.trace = lambda kind, **payload: recorder.observe(
            kind, product_session_id=SESSION, **payload)
        await self.voice.configure_session({"voice": "test"})
        self.voice.ready.set()
        await self.voice.submit_text("照常回答")
        recorder.turn_prepared(SESSION, memory={"status": "empty"})
        recorder.flush()
        self.assertEqual(json.loads(self.voice.ws.sent[-1])["type"], "response.create")
        self.assertTrue(failures)
        self.assertEqual(set(failures), {"context_ledger.record_failed"})


class InteractionTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_recall_latency_and_status_are_reported(self):
        from boxagent.core.events import StateEvents
        from boxagent.domain.interaction.service import InteractionService

        observed = []

        class Observer:
            def turn_started(self, session_id, *, channel):
                observed.append(("start", session_id, channel))

            def turn_prepared(self, session_id, **data):
                observed.append(("prepare", session_id, data))

        class Memory:
            async def context_packet(self, query, *, session_id, top_k):
                await asyncio.sleep(0.01)
                return "<boxagent_memory_context>x</boxagent_memory_context>"

        class Failing:
            async def context_packet(self, *_args, **_kwargs):
                raise TimeoutError

        class Voice:
            injected = []

            async def inject_memory_context(self, packet):
                self.injected.append(packet)

        context = type("Ctx", (), {"session": type("S", (), {"session_id": SESSION})()})()
        service = InteractionService(StateEvents(lambda _e: None), None, None,
                                     memory=Memory(), context_observer=Observer())
        service.voice = Voice()
        await service._inject_memory_context("q", context)
        service.memory = Failing()
        await service._inject_memory_context("q", context)

        ok, failed = observed[0][2]["memory"], observed[1][2]["memory"]
        self.assertEqual(ok["status"], "injected")
        self.assertGreaterEqual(ok["ms"], 5)
        self.assertEqual(failed["status"], "failed:TimeoutError")
        self.assertEqual(len(Voice.injected), 1)


class ApplicationSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_reports_corrupt_ledger_without_raising(self):
        from boxagent.application.assistant import BoxAgentApplication

        with tempfile.TemporaryDirectory() as directory:
            store = JsonlContextLedgerStore(Path(directory))
            store.append(SESSION, "turn/start", {})
            store.append(SESSION, "turn/end", {})
            lines = store.path(SESSION).read_text(encoding="utf-8").splitlines()
            lines[1] = "{broken"
            store.path(SESSION).write_text("\n".join(lines) + "\n", encoding="utf-8")
            application = BoxAgentApplication(
                lambda _event: None, None, None, context_ledger=store)
            snapshot = await application.context_snapshot(SESSION)
            self.assertEqual(snapshot["status"], "corrupt")

    async def test_snapshot_and_entry_round_trip(self):
        from boxagent.application.assistant import BoxAgentApplication

        with tempfile.TemporaryDirectory() as directory:
            store = JsonlContextLedgerStore(Path(directory))
            store.append(SESSION, "context/rebuild", {"model": "m"})
            long_text = "长" * 400
            stored = store.append(SESSION, "system/message", {
                "category": "memory", "content": long_text,
                "chars": 400, "tokens": 400})
            application = BoxAgentApplication(
                lambda _event: None, None, None, context_ledger=store)
            snapshot = await application.context_snapshot(SESSION)
            self.assertEqual(snapshot["status"], "ready")
            self.assertLess(len(snapshot["entries"][0]["preview"]), 200)
            entry = await application.context_entry(SESSION, stored.seq)
            self.assertEqual(entry["content"], long_text)


class TraceHistoryFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_ledger_session_is_rebuilt_from_global_trace(self):
        from boxagent.application.assistant import BoxAgentApplication
        from boxagent.infrastructure.context.qwen_trace_history import QwenTraceHistory

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "events.jsonl"
            rows = [
                {"event": "client.request", "occurred_at": 1.0,
                 "product_session_id": SESSION,
                 "request": {"type": "session.update",
                             "session": {"instructions": "规则", "tools": []}}},
                {"event": "client.request", "occurred_at": 2.0,
                 "product_session_id": SESSION,
                 "request": {"type": "conversation.item.create", "item": {
                     "type": "message", "role": "system", "content": [
                         {"type": "input_text",
                          "text": "<boxagent_memory_context>x</boxagent_memory_context>"}]}}},
                {"event": "client.request", "occurred_at": 2.1,
                 "product_session_id": "ses_other000000000",
                 "request": {"type": "conversation.item.create", "item": {
                     "type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "别的会话"}]}}},
                {"event": "client.request", "occurred_at": 2.2,
                 "product_session_id": SESSION,
                 "request": {"type": "conversation.item.create", "item": {
                     "type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "你好"}]}}},
            ]
            trace.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                        for row in rows) + "\n", encoding="utf-8")
            application = BoxAgentApplication(
                lambda _event: None, None, None,
                context_ledger=JsonlContextLedgerStore(Path(directory)),
                context_history=QwenTraceHistory(
                    trace, lambda store, clock: QwenContextRecorder(store, clock=clock)))
            snapshot = await application.context_snapshot(SESSION)
            self.assertEqual(snapshot["source"], "trace_replay")
            self.assertEqual([entry["category"] for entry in snapshot["entries"]],
                             ["instructions", "tools", "memory", "user"])
            self.assertEqual(len(snapshot["turns"]), 1)
            entry = await application.context_entry(SESSION, snapshot["entries"][-1]["seq"])
            self.assertEqual(entry["content"], "你好")
            # Reconstruction is read-only.
            self.assertFalse(JsonlContextLedgerStore(Path(directory)).path(SESSION).exists())

            empty = await application.context_snapshot("ses_nothing0000000")
            self.assertEqual(empty["status"], "empty")


if __name__ == "__main__":
    unittest.main()
