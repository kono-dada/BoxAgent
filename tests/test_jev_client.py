"""JevClient 分批与时间排序；需要 Jev-Mem 解释器（含 typesafe_sdk），主测试环境自动跳过。

    .runtime/jev-mem-venv/bin/python -m pytest tests/test_jev_client.py
"""

import unittest
from datetime import datetime, timezone

try:
    from typesafe_sdk import Noul

    from boxagent.infrastructure.memory.jev_mem.core.jev_client import JevClient, ProbabilityResult
    from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig
    from boxagent.infrastructure.memory.jev_mem.core.trg_memory import _time_key
except ModuleNotFoundError:  # pragma: no cover - main venv lacks the SDK
    JevClient = None


@unittest.skipIf(JevClient is None, "需要 Jev-Mem 解释器")
class JevClientBatchingTests(unittest.TestCase):
    def setUp(self):
        self.client = JevClient(JevMemConfig(max_retries=0, cache_size=0), api_key="k")
        self.calls = []

        def once(operation, state, questions, *, mock_values=None, budget=None):
            self.calls.append(list(questions))
            return ProbabilityResult({name: 0.5 for name in questions}, "jev",
                                     model="decision-model-preview", usage={"input_tokens": 10})

        self.client._evaluate_once = once

    def questions(self, count):
        return {f"q{i}": Noul(instructions=f"Is `x` about {i}?") for i in range(count)}

    def test_large_batches_are_split_at_the_provider_limit(self):
        result = self.client.evaluate("relations", {"x": 1}, self.questions(40))
        self.assertEqual([len(call) for call in self.calls], [16, 16, 8])
        self.assertEqual(set(result.values), {f"q{i}" for i in range(40)})
        self.assertEqual(result.usage["input_tokens"], 30)

    def test_small_batches_use_one_request(self):
        self.client.evaluate("observation", {"x": 1}, self.questions(9))
        self.assertEqual(len(self.calls), 1)

    def test_any_failed_chunk_fails_the_batch(self):
        def fail_second(operation, state, questions, **_):
            self.calls.append(list(questions))
            return None if len(self.calls) == 2 else ProbabilityResult(
                {name: 0.5 for name in questions}, "jev")

        self.client._evaluate_once = fail_second
        self.assertIsNone(self.client.evaluate("relations", {"x": 1}, self.questions(40)))
        self.assertEqual(len(self.calls), 2)


@unittest.skipIf(JevClient is None, "需要 Jev-Mem 解释器")
class TemporalOrderTests(unittest.TestCase):
    def test_untimed_naive_and_aware_nodes_sort_together(self):
        class Node:
            def __init__(self, timestamp):
                self.timestamp = timestamp

        nodes = [Node(datetime(2026, 10, 7, tzinfo=timezone.utc)), Node(None),
                 Node(datetime(2026, 10, 6))]
        ordered = sorted(nodes, key=_time_key)
        self.assertIsNone(ordered[0].timestamp)
        self.assertEqual(ordered[1].timestamp, datetime(2026, 10, 6))


if __name__ == "__main__":
    unittest.main()
