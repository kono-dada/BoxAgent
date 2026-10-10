"""REQ-01: Session Context Ledger store, estimate and projection."""

import json
import tempfile
import unittest
from pathlib import Path

from boxagent.domain.context import (
    LedgerEvent,
    distribute_percent,
    estimate_tokens,
    project_context,
)
from boxagent.infrastructure.persistence.jsonl_context_ledger import (
    FILE_NAME,
    JsonlContextLedgerStore,
)


SESSION = "ses_0123456789abcdef"


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


if __name__ == "__main__":
    unittest.main()
