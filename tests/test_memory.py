"""Jev-Mem-first delivery, projection, retrieval and worker contracts."""

import asyncio
from dataclasses import replace
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from boxagent.application.memory import MemoryModule
from boxagent.application.memory_context import MemoryContextProvider
from boxagent.application.memory_ingestion import MemoryIngestionCoordinator
from boxagent.application.memory_migration import LegacyCanonicalMemoryImporter
from boxagent.application.memory_narrative import NarrativeProjector
from boxagent.application.memory_profile import (
    DeepSeekProfileConstructor, JsonProfileStore, ProfileProjector,
)
from boxagent.domain.conversation import ConversationService
from boxagent.domain.conversation.models import ContextCheckpoint, RuntimeContextSegment
from boxagent.domain.memory.contracts import MemoryUnavailable
from boxagent.domain.memory.policy import contains_secret, redact_memory_source
from boxagent.domain.memory.service import MemoryService
from boxagent.infrastructure.memory.jev_mem import JevMemWorker
from boxagent.infrastructure.memory.jev_mem.worker import Worker
from boxagent.infrastructure.persistence import JsonlSessionStore, JsonMemoryJobStore


class FakeAudit:
    def __init__(self):
        self.events = []

    def emit(self, event, **values):
        self.events.append((event, values))


class FakeNode:
    def __init__(self, node_id, content, node_type="EVENT"):
        self.node_id = node_id
        self.content_narrative = content
        self.timestamp = None
        self.node_type = node_type
        self.attributes = {"raw_content": content}


class FakeLink:
    def __init__(self, link_id, source, target):
        self.link_id = link_id
        self.source_node_id = source
        self.target_node_id = target
        self.link_type = "TEMPORAL"
        self.properties = {"sub_type": "PRECEDES", "private_score": .99}
        self.metadata = {"private": "not exposed"}


class FakeGraph:
    def __init__(self, nodes, links=()):
        self.nodes = {node.node_id: node for node in nodes}
        self.links = {link.link_id: link for link in links}

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def delete_node(self, node_id):
        return self.nodes.pop(node_id, None) is not None


class FakeVectors:
    def __init__(self, ids):
        self.ids = set(ids)

    def exists(self, node_id):
        return node_id in self.ids

    def delete_vector(self, node_id):
        if node_id not in self.ids:
            return False
        self.ids.remove(node_id)
        return True


class FakeSystem:
    def __init__(self):
        nodes = [FakeNode("memory-1", "用户喜欢简洁回答"),
                 FakeNode("memory-2", "另一条记忆")]
        self.graph_db = FakeGraph(nodes, [FakeLink("link-1", "memory-1", "memory-2")])
        self.vector_db = FakeVectors(node.node_id for node in nodes)
        builder = type("Builder", (), {})()
        builder.node_index = {"简洁": {"memory-1"},
                              "其他": {"memory-1", "memory-2"}}
        builder.jev = type("Jev", (), {"audit": FakeAudit()})()
        self.memory_builder = builder
        self.query_engine = type("Query", (), {"node_index": builder.node_index})()
        self.saved = False

    def save_memory(self):
        self.saved = True


class RecordingBackend:
    def __init__(self, *, admitted=True, fail_times=0):
        self.admitted = admitted
        self.fail_times = fail_times
        self.remember_attempts = 0
        self.observations = []
        self.queries = []
        self.nodes = []

    async def health(self):
        return {"status": "ready", "backend": "fake",
                "memory_count": len(self.nodes)}

    async def remember(self, observations):
        self.remember_attempts += 1
        if self.remember_attempts <= self.fail_times:
            raise RuntimeError("temporary Jev outage")
        self.observations.extend(observations)
        created = []
        if self.admitted:
            for observation in observations:
                node = {
                    "id": f"jev-{len(self.nodes) + 1}", "type": "EVENT",
                    "content": observation["content"],
                    "timestamp": observation.get("timestamp"),
                    "metadata": {
                        **observation.get("metadata", {}),
                        "jev_mem": {"admission_score": .95,
                                    "memory_type": {"preference": .9,
                                                    "semantic": .7,
                                                    "procedural": .1,
                                                    "episodic": .2}},
                    },
                }
                self.nodes.append(node)
                created.append(node)
        return {"admitted": len(created),
                "rejected": len(observations) - len(created),
                "created": created, "memory_count": len(self.nodes)}

    async def remember_narrative(self, checkpoint, *, metadata=None):
        node = {"id": f"narrative-{len(self.nodes) + 1}",
                "type": "NARRATIVE", "content": checkpoint["summary"],
                "timestamp": None, "metadata": dict(metadata or {})}
        self.nodes.append(node)
        return {"created": [node], "memory_count": len(self.nodes)}

    async def query(self, question, *, top_k=5, mode="deep"):
        self.queries.append((question, top_k, mode))
        return {"memories": self.nodes[:top_k], "evidence": "fake evidence",
                "trace": {"mode": mode}}

    async def inspect(self, *, query="", selected_id=None,
                      node_limit=100, edge_limit=200):
        del query, edge_limit
        nodes = [node for node in self.nodes
                 if selected_id is None or node["id"] == selected_id]
        return {"nodes": nodes[:node_limit], "edges": [],
                "selected_id": selected_id, "truncated": False,
                "statistics": {"node_count": len(self.nodes), "edge_count": 0,
                               "matched_count": len(nodes), "node_types": {},
                               "link_types": {}}}

    async def forget(self, memory_ids):
        requested = set(memory_ids)
        deleted = [node for node in self.nodes if node["id"] in requested]
        self.nodes = [node for node in self.nodes if node["id"] not in requested]
        deleted_ids = {node["id"] for node in deleted}
        return {"deleted": deleted,
                "missing": [item for item in memory_ids if item not in deleted_ids],
                "memory_count": len(self.nodes)}

    async def close(self):
        return None


class MemoryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        fixture = Path(__file__).parent / "fixtures/memory_worker.py"
        self.worker = JevMemWorker(
            command=[sys.executable, str(fixture)],
            log_path=Path(self.directory.name) / "worker.log",
            startup_timeout=2, request_timeout=2)

    async def asyncTearDown(self):
        await self.worker.close()
        self.directory.cleanup()

    async def test_round_trip_modes_narrative_and_forget(self):
        self.assertEqual((await self.worker.health())["backend"], "fixture")
        written = await self.worker.remember([{"content": "用户喜欢简洁回答"}])
        narrative = await self.worker.remember_narrative(
            {"summary": "用户在设计 BoxAgent"},
            metadata={"checkpoint_id": "ckp-1"})
        self.assertEqual(narrative["created"][0]["type"], "NARRATIVE")
        direct = await self.worker.query("偏好", mode="direct")
        deep = await self.worker.query("过往设计", mode="deep")
        self.assertEqual(direct["trace"]["mode"], "direct")
        self.assertEqual(deep["trace"]["mode"], "deep")
        self.assertEqual({item["type"] for item in deep["memories"]},
                         {"EVENT", "NARRATIVE"})
        forgotten = await self.worker.forget([written["created"][0]["id"]])
        self.assertEqual(len(forgotten["deleted"]), 1)
        self.assertTrue((await self.worker.save())["saved"])

    async def test_validation_error_and_cancel_reset_protocol(self):
        with self.assertRaisesRegex(ValueError, "非空列表"):
            await self.worker.remember([])
        with self.assertRaisesRegex(ValueError, "direct 或 deep"):
            await self.worker.query("测试", mode="other")
        self.assertIsNone(self.worker.process)
        with self.assertRaisesRegex(MemoryUnavailable, "expected failure"):
            await self.worker._request("fail")
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.worker._request("slow"), .05)
        self.assertIsNone(self.worker.process)
        self.assertEqual((await self.worker.health())["status"], "ready")

    async def test_restart_uses_startup_timeout_again(self):
        self.worker.startup_timeout = .5
        self.worker.request_timeout = .01
        with self.assertRaises(MemoryUnavailable):
            await self.worker._request("slow")
        self.assertIsNone(self.worker.process)
        self.assertEqual((await self.worker.health())["status"], "ready")

    def test_jev_backend_requires_key(self):
        worker = JevMemWorker(backend="jev", command=[sys.executable, "unused"])
        with self.assertRaisesRegex(MemoryUnavailable, "TYPESAFE_API_KEY"):
            worker._environment()

    def test_forget_removes_graph_vector_and_keyword_indexes(self):
        worker = Worker.__new__(Worker)
        worker.system = FakeSystem()
        result = worker.handle({"operation": "forget",
                                "memory_ids": ["memory-1", "missing"]})
        self.assertEqual([item["id"] for item in result["deleted"]], ["memory-1"])
        self.assertNotIn("memory-1", worker.system.graph_db.nodes)
        self.assertNotIn("memory-1", worker.system.vector_db.ids)
        self.assertNotIn("简洁", worker.system.memory_builder.node_index)
        self.assertTrue(worker.system.saved)

    def test_worker_deduplicates_automatic_and_explicit_same_source_event(self):
        class MemoryBuilder:
            def __init__(self, graph):
                self.graph = graph

            def build(self, content, timestamp, metadata):
                node = FakeNode(f"memory-{len(self.graph.nodes) + 1}", content)
                node.attributes = dict(metadata)
                self.graph.nodes[node.node_id] = node
                return node

        class System:
            def __init__(self):
                self.graph_db = FakeGraph([], [])
                self.memory_builder = MemoryBuilder(self.graph_db)
                self.saved = False

            def build_memory_from_conversation(self, observations):
                admitted = sum(
                    self.memory_builder.build(
                        item["content"], None, item.get("metadata", {})) is not None
                    for item in observations)
                return {"admitted": admitted, "rejected": 0}

            def save_memory(self):
                self.saved = True

        worker = Worker.__new__(Worker)
        worker.system = System()
        first = worker.handle({"operation": "remember", "observations": [{
            "content": "我喜欢乌龙茶，而且希望回答简洁。",
            "metadata": {"source": "automatic", "source_event_id": "evt-1"},
        }]})
        second = worker.handle({"operation": "remember", "observations": [{
            "content": "用户喜欢乌龙茶，且希望回答简洁。",
            "metadata": {"source": "voice_explicit", "explicit": True,
                         "source_event_ids": ["evt-1"]},
        }]})

        self.assertEqual(first["memory_count"], 1)
        self.assertEqual(second["memory_count"], 1)
        self.assertEqual(second["deduplicated"], 1)
        self.assertEqual(second["reused"][0]["id"], "memory-1")
        attributes = worker.system.graph_db.nodes["memory-1"].attributes
        self.assertTrue(attributes["explicit"])
        self.assertEqual(attributes["source_event_ids"], ["evt-1"])

    def test_inspect_is_sanitized_and_one_hop(self):
        worker = Worker.__new__(Worker)
        nodes = [FakeNode("a", "A"), FakeNode("b", "B"), FakeNode("c", "C")]
        worker.system = type("System", (), {
            "graph_db": FakeGraph(nodes, [FakeLink("ab", "a", "b"),
                                           FakeLink("bc", "b", "c")])})()
        result = worker.handle({"operation": "inspect", "selected_id": "a",
                                "node_limit": 10, "edge_limit": 10})
        self.assertEqual({node["id"] for node in result["nodes"]}, {"a", "b"})
        self.assertEqual([edge["id"] for edge in result["edges"]], ["ab"])
        self.assertNotIn("raw_content", json.dumps(result, ensure_ascii=False))


class IngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_commit_enqueues_immediately_and_assistant_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            jobs = JsonMemoryJobStore(root / "memory")
            backend = RecordingBackend()
            ingestion = MemoryIngestionCoordinator(
                sessions, jobs, service=MemoryService(backend, jobs))
            conversation = ConversationService(
                sessions, message_sinks=(ingestion,),
                finalization_sinks=(ingestion,))
            await conversation.start()
            await ingestion.start()
            context = await conversation.begin_interaction("我喜欢爵士乐")
            self.assertEqual(len(await jobs.jobs()), 1)
            await conversation.finish_interaction(
                context, status="succeeded", assistant_content="好呀")
            await ingestion.drain()
            stored = await jobs.jobs()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].status, "completed")
            self.assertEqual(stored[0].result["admitted"], 1)
            self.assertEqual([item["content"] for item in backend.observations],
                             ["我喜欢爵士乐"])
            await ingestion.close()

    async def test_idempotency_and_restart_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            conversation = ConversationService(sessions)
            await conversation.start()
            context = await conversation.begin_interaction("我偏好简洁回答")
            jobs = JsonMemoryJobStore(root / "memory")
            await jobs.start()
            arguments = dict(session_id=context.session.session_id,
                             interaction_id=context.interaction_id,
                             source_hash="same",
                             admission_version="jev-observation-v1")
            first = await jobs.enqueue_job(**arguments)
            second = await jobs.enqueue_job(**arguments)
            self.assertEqual(first.job_id, second.job_id)

            reopened = JsonMemoryJobStore(root / "memory")
            backend = RecordingBackend()
            ingestion = MemoryIngestionCoordinator(
                sessions, reopened, service=MemoryService(backend, reopened))
            await ingestion.start()
            await ingestion.drain()
            restored = (await reopened.jobs())[0]
            self.assertEqual(restored.status, "completed")
            self.assertEqual(restored.attempts, 1)
            self.assertEqual(backend.observations[0]["content"], "我偏好简洁回答")
            await ingestion.close()

    async def test_failure_retries_with_durable_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            jobs = JsonMemoryJobStore(root / "memory")
            backend = RecordingBackend(fail_times=2)
            ingestion = MemoryIngestionCoordinator(
                sessions, jobs, service=MemoryService(backend, jobs),
                retry_base_seconds=.01)
            conversation = ConversationService(sessions, message_sinks=(ingestion,))
            await conversation.start()
            await ingestion.start()
            await conversation.begin_interaction("我喜欢摇滚乐")
            await ingestion.drain()
            job = (await jobs.jobs())[0]
            self.assertEqual((job.status, job.attempts), ("completed", 3))
            history = (root / "memory/jobs/ingestion.jsonl").read_text()
            self.assertIn('"status":"retry_wait"', history)
            await ingestion.close()

    async def test_secret_is_rejected_before_jev(self):
        backend = RecordingBackend()
        result = await MemoryService(backend).observe_user_message(
            "API_KEY=sk-example-only-1234567890", session_id="ses-1",
            interaction_id="int-1", source_event_id="evt-1")
        self.assertEqual(result["reason"], "secret")
        self.assertEqual(backend.observations, [])
        self.assertTrue(contains_secret("密码: example-only-not-real"))
        self.assertNotIn("example-only-not-real",
                         redact_memory_source("密码: example-only-not-real"))


class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_records_import_once_and_inactive_records_stay_out(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "memory/ledger/snapshot.json"
            marker = root / "memory/migrations/canonical-ledger-v1.json"
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(json.dumps({
                "schema_version": 1,
                "records": {
                    "mem-active": {
                        "memory_id": "mem-active", "revision": 2,
                        "content": "用户偏好简洁回答。", "status": "active",
                        "kind": "profile", "source_mode": "automatic",
                        "source_session_id": "ses-1",
                        "source_interaction_id": "int-1",
                        "source_event_ids": ["evt-1"],
                        "backend_links": [], "created_at": 1,
                        "updated_at": 2,
                    },
                    "mem-deleted": {
                        "memory_id": "mem-deleted", "revision": 3,
                        "content": "不应复活", "status": "deleted",
                        "backend_links": [],
                    },
                },
            }), encoding="utf-8")
            backend = RecordingBackend()
            importer = LegacyCanonicalMemoryImporter(
                backend, snapshot, marker)
            await importer.start()
            await importer.drain()
            self.assertEqual((await importer.health())["status"], "completed")
            self.assertEqual([item["content"] for item in backend.observations],
                             ["用户偏好简洁回答。"])
            metadata = backend.observations[0]["metadata"]
            self.assertTrue(metadata["explicit"])
            self.assertEqual(metadata["legacy_canonical_id"], "mem-active")
            self.assertTrue(marker.is_file())

            repeated = LegacyCanonicalMemoryImporter(backend, snapshot, marker)
            await repeated.start()
            await repeated.drain()
            self.assertEqual(len(backend.observations), 1)

    async def test_existing_backend_link_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot.json"
            snapshot.write_text(json.dumps({
                "schema_version": 1,
                "records": {"mem-1": {
                    "memory_id": "mem-1", "revision": 1,
                    "content": "已经在 Jev 中", "status": "active",
                    "backend_links": ["jev-1"],
                }},
            }), encoding="utf-8")
            backend = RecordingBackend()
            backend.nodes.append({"id": "jev-1", "type": "EVENT",
                                  "content": "已经在 Jev 中",
                                  "timestamp": None, "metadata": {}})
            importer = LegacyCanonicalMemoryImporter(
                backend, snapshot, root / "marker.json")
            await importer.start()
            await importer.drain()
            health = await importer.health()
            self.assertEqual(health["already_present"], 1)
            self.assertEqual(backend.observations, [])


class ProjectionAndRecallTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_build_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonProfileStore(Path(directory) / "profile.json")
            backend = RecordingBackend()
            projector = ProfileProjector(
                store, DeepSeekProfileConstructor(api_key=""))
            service = MemoryService(backend, profile_projector=projector)
            remembered = await service.remember("我偏好简洁回答")
            profile = await store.load()
            self.assertEqual(
                profile["fields"]["preferences.communication.answer_style"]["value"],
                "简洁")
            await service.delete_node(remembered["memory_id"])
            self.assertEqual((await store.load())["fields"], {})

    async def test_l2_direct_split_and_l3_deep(self):
        backend = RecordingBackend()
        backend.nodes = [
            {"id": "event-1", "type": "EVENT", "content": "用户喜欢爵士乐",
             "timestamp": None, "metadata": {}},
            {"id": "narrative-1", "type": "NARRATIVE",
             "content": "用户长期在设计 BoxAgent", "timestamp": None,
             "metadata": {"checkpoint_id": "ckp-1"}},
        ]
        service = MemoryService(backend)
        context = await service.prepare_context("继续聊昨天的设计", top_k=5)
        self.assertEqual(backend.queries[0], ("继续聊昨天的设计", 5, "direct"))
        self.assertEqual([item["id"] for item in context["evidence"]], ["event-1"])
        self.assertEqual([item["id"] for item in context["narrative"]],
                         ["narrative-1"])
        recalled = await service.recall("过往音乐偏好", top_k=3, mode="deep")
        self.assertEqual(backend.queries[-1][2], "deep")
        self.assertNotIn("metadata", recalled["memories"][0])

    async def test_context_packet_contains_profile_fact_and_narrative(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_store = JsonProfileStore(Path(directory) / "profile.json")
            await profile_store.apply([{
                "op": "replace",
                "path": "preferences.communication.answer_style",
                "value": "简洁", "confidence": .95,
            }], memory_id="event-1")
            backend = RecordingBackend()
            backend.nodes = [
                {"id": "event-1", "type": "EVENT", "content": "用户喜欢爵士乐",
                 "timestamp": None, "metadata": {}},
                {"id": "narrative-1", "type": "NARRATIVE",
                 "content": "用户正在实现 BoxAgent", "timestamp": None,
                 "metadata": {"checkpoint_id": "ckp-1"}},
            ]
            module = MemoryModule(
                MemoryService(backend),
                context=MemoryContextProvider(backend, profile_store))
            packet = await module.context_packet("继续做", session_id="ses-1")
            self.assertIn("answer_style", packet)
            self.assertIn("用户喜欢爵士乐", packet)
            self.assertIn("用户正在实现 BoxAgent", packet)
            evidence = await module.evidence("继续做", session_id="ses-1")
            self.assertEqual({item["kind"] for item in evidence},
                             {"profile", "event", "narrative"})

    async def test_checkpoint_projects_narrative(self):
        backend = RecordingBackend()
        projector = NarrativeProjector(backend)
        checkpoint = ContextCheckpoint(
            checkpoint_id="ckp-1", session_id="ses-1",
            runtime="qwen_realtime", source_from_sequence=1,
            covered_through_sequence=4, source_hash="hash", created_at=1,
            provider="deepseek", model="deepseek-flash",
            content={"summary": "用户在设计 BoxAgent 的记忆系统",
                     "user_facts": [], "decisions": [], "outcomes": [],
                     "open_loops": [], "entities": ["BoxAgent"],
                     "commitments": [],
                     "time_range": {"start": None, "end": None},
                     "salient_events": [{"event_id": "evt-1",
                                          "description": "讨论记忆"}]})
        segment = RuntimeContextSegment(
            segment_id="seg-1", session_id="ses-1", runtime="qwen_realtime",
            start_sequence=1, end_sequence=4, checkpoint_id="ckp-1", created_at=1)
        result = await projector.checkpoint_created(checkpoint, segment)
        self.assertEqual(result["created"][0]["type"], "NARRATIVE")
        self.assertEqual(result["created"][0]["metadata"]["segment_id"], "seg-1")

    async def test_prewarm_does_not_block(self):
        ready, release = asyncio.Event(), asyncio.Event()

        class Backend(RecordingBackend):
            async def health(self):
                ready.set()
                await release.wait()
                return {"status": "ready", "memory_count": 0}

        service = MemoryService(Backend())
        await asyncio.wait_for(service.start(), .1)
        await asyncio.wait_for(ready.wait(), .1)
        self.assertFalse(service.prewarm_task.done())
        release.set()
        await service.prewarm_task
        await service.close()


class JobStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_append_only_rebuild_and_torn_tail_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "memory"
            store = JsonMemoryJobStore(root)
            await store.start()
            job = await store.enqueue_job(
                session_id="ses-1", interaction_id="int-1",
                source_hash="source", admission_version="jev-v1")
            await store.update_job(replace(
                job, status="completed", attempts=1, updated_at=time.time(),
                result={"observation_count": 1, "admitted": 1, "rejected": 0}))
            with (root / "jobs/ingestion.jsonl").open("a", encoding="utf-8") as stream:
                stream.write('{"broken":')
            reopened = JsonMemoryJobStore(root)
            await reopened.start()
            restored = await reopened.jobs()
            self.assertEqual((len(restored), restored[0].status), (1, "completed"))
            self.assertNotIn("broken", (root / "jobs/ingestion.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
