"""JevClient 分批与时间排序；需要 Jev-Mem 解释器（含 typesafe_sdk），主测试环境自动跳过。

    .runtime/jev-mem-venv/bin/python -m pytest tests/test_jev_client.py
"""

import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

try:
    from typesafe_sdk import Noul

    from boxagent.infrastructure.memory.jev_mem.core.jev_client import JevClient, JevUnavailable, ProbabilityResult
    from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig
    from boxagent.infrastructure.memory.jev_mem.core.trg_memory import _time_key
    from boxagent.infrastructure.memory.jev_mem.core.memory_builder import MemoryBuilder, MemoryAdmissionUnavailable, MemoryWriteUnavailable
    from boxagent.infrastructure.memory.jev_mem.core.jev_mem_policies import MemoryAdmission, MemoryTypeScores
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


@unittest.skipIf(JevClient is None, "需要 Jev-Mem 解释器")
class AutomaticAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        # Real build control flow; no local encoder/model or cloud calls.
        self.builder = object.__new__(MemoryBuilder)
        self.builder.jev_config = JevMemConfig(write_enabled=True, admission_enabled=True)
        self.builder.jev = Mock()
        self.builder.trg = Mock()
        self.builder.trg.graph_db.nodes = {}
        self.builder.write_policy = Mock()
        self.builder._build_magma = Mock()
        self.metadata = {"source": "automatic", "source_event_id": "evt-fixture"}

    def test_admission_outage_never_falls_back_to_unchecked_storage(self) -> None:
        self.builder.write_policy.assess_observation.return_value = None
        with self.assertRaises(MemoryAdmissionUnavailable):
            self.builder.build("我喜欢爵士乐", metadata=self.metadata)
        self.builder._build_magma.assert_not_called()
        self.builder.trg.vector_db.add_vector.assert_not_called()
        self.builder.trg.graph_db.add_node.assert_not_called()
        self.builder.jev.audit.emit.assert_called_with(
            "memory_admission_unavailable", source_event_id="evt-fixture")

    def test_filler_is_rejected_by_the_decision_model(self) -> None:
        self.builder.write_policy.assess_observation.return_value = (
            MemoryAdmission(.01, .1, .1, .1, 0), MemoryTypeScores(.1, .1, .1, .1))
        for text in ("？", "可以", "好的！", "OK"):
            self.assertIsNone(self.builder.build(text, metadata=self.metadata))
        self.assertEqual(self.builder.write_policy.assess_observation.call_count, 4)
        self.builder._build_magma.assert_not_called()

    def test_provider_rejection_stays_rejected(self) -> None:
        self.builder.write_policy.assess_observation.return_value = (
            MemoryAdmission(.01, .1, .1, .1, 0), MemoryTypeScores(.1, .1, .1, .1))
        self.assertIsNone(self.builder.build("我在等这次回复", metadata=self.metadata))
        self.builder._build_magma.assert_not_called()

    def test_explicit_write_requires_a_successful_type_decision(self) -> None:
        self.builder.write_policy.memory_type.return_value = None
        with self.assertRaises(MemoryWriteUnavailable):
            self.builder.build("项目名称是 OK", metadata={**self.metadata, "explicit": True})
        self.builder.write_policy.assess_observation.assert_not_called()
        self.builder._build_magma.assert_not_called()
        self.builder.trg.vector_db.add_vector.assert_not_called()
        self.builder.trg.graph_db.add_node.assert_not_called()

    def _prepare_admitted_write(self) -> None:
        self.builder.write_policy.assess_observation.return_value = (
            MemoryAdmission(.95, .9, .9, .9, 0), MemoryTypeScores(.1, .8, .1, .9))
        self.builder.temporal_parser = Mock()
        self.builder.temporal_parser.describe_references.return_value = []
        self.builder.trg.keyword_enricher.extract_keywords.return_value = ["爵士"]
        self.builder.trg.keyword_enricher.enrich_content.return_value = "我喜欢爵士乐"
        self.builder.trg.encoder.encode.return_value = [1., 0.]
        self.builder.trg.vector_db.search.return_value = []
        self.builder._jev_writes = 0
        self.builder.index_event = Mock()
        self.builder._store_jev_node = Mock(return_value=0)

    def test_relation_outage_inserts_neither_graph_nor_vector(self) -> None:
        self._prepare_admitted_write()
        self.builder.write_policy.relations.return_value = None
        with self.assertRaises(MemoryWriteUnavailable):
            self.builder.build("我喜欢爵士乐", metadata=self.metadata)
        self.builder._store_jev_node.assert_not_called()
        self.builder._build_magma.assert_not_called()
        self.builder.trg.vector_db.add_vector.assert_not_called()
        self.builder.trg.graph_db.add_node.assert_not_called()

    def test_successful_decisions_continue_to_canonical_insertion(self) -> None:
        self._prepare_admitted_write()
        self.builder.write_policy.relations.return_value = []
        node = self.builder.build("我喜欢爵士乐", metadata=self.metadata)
        self.assertEqual(node.content_narrative, "我喜欢爵士乐")
        self.builder._store_jev_node.assert_called_once_with(node, [])
        self.builder._build_magma.assert_not_called()

    def test_strict_client_propagates_failure_instead_of_mock_decisions(self) -> None:
        client = JevClient(JevMemConfig(fallback_to_magma=False, max_retries=0), api_key="")
        with self.assertRaises(JevUnavailable):
            client.evaluate("observation", {"observation": "我喜欢猫"},
                            {"should_store": Noul(instructions="Should this be stored?")},
                            mock_values={"should_store": .95})

    def test_production_worker_requires_strict_decisions(self) -> None:
        from pathlib import Path
        from boxagent.infrastructure.memory.jev_mem.worker import Worker
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "fixture"}), \
                patch("boxagent.infrastructure.memory.jev_mem.api.JevMemSystem") as system, \
                patch("boxagent.infrastructure.memory.jev_mem.core.query_engine.QueryEngine"):
            Worker(Path("unused-fixture-cache"), "auto")
        config = system.call_args.kwargs["jev_config"]
        self.assertTrue(config.write_enabled)
        self.assertTrue(config.admission_enabled)
        self.assertFalse(config.jev_mock)
        self.assertFalse(config.fallback_to_magma)


if __name__ == "__main__":
    unittest.main()
