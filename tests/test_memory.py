"""Jev-Mem Worker 适配器的进程与协议契约。"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from boxagent.application.memory_context import MemoryContextProvider
from boxagent.application.memory_extraction import MemoryExtractionCoordinator
from boxagent.domain.conversation import ConversationService
from boxagent.domain.memory.contracts import MemoryUnavailable
from boxagent.domain.memory.models import CanonicalMemory, MemoryCandidate
from boxagent.domain.memory.policy import (
    contains_secret,
    memory_admission,
    redact_memory_source,
)
from boxagent.domain.memory.service import MemoryService
from boxagent.infrastructure.memory.jev import JevMemoryWorker
from boxagent.infrastructure.persistence import JsonlSessionStore, JsonMemoryLedger
from scripts.jev_memory_worker import Worker


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
    def __init__(self, link_id, source, target, link_type="TEMPORAL", subtype="PRECEDES"):
        self.link_id = link_id
        self.source_node_id = source
        self.target_node_id = target
        self.link_type = link_type
        self.properties = {"sub_type": subtype, "private_score": 0.99}
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


class FakeBuilder:
    def __init__(self):
        self.node_index = {"简洁": {"memory-1"}, "其他": {"memory-1", "memory-2"}}
        self.jev = type("Jev", (), {"audit": FakeAudit()})()


class FakeSystem:
    def __init__(self):
        nodes = [FakeNode("memory-1", "用户喜欢简洁回答"), FakeNode("memory-2", "另一条记忆")]
        links = [FakeLink("link-1", "memory-1", "memory-2")]
        self.graph_db = FakeGraph(nodes, links)
        self.vector_db = FakeVectors(node.node_id for node in nodes)
        self.memory_builder = FakeBuilder()
        self.query_engine = type("Query", (), {"node_index": self.memory_builder.node_index})()
        self.saved = False

    def save_memory(self):
        self.saved = True


class MemoryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        fixture = Path(__file__).parent / "fixtures/memory_worker.py"
        self.worker = JevMemoryWorker(command=[sys.executable, str(fixture)],
                                      log_path=Path(self.directory.name) / "worker.log",
                                      startup_timeout=2, request_timeout=2)

    async def asyncTearDown(self):
        await self.worker.close()
        self.directory.cleanup()

    async def test_worker_round_trip_and_order(self):
        health = await self.worker.health()
        self.assertEqual(health["backend"], "fixture")
        written = await self.worker.remember([{"content": "用户喜欢简洁回答"}])
        self.assertEqual(written["admitted"], 1)
        self.assertEqual(written["created"][0]["content"], "用户喜欢简洁回答")
        result = await self.worker.query("用户喜欢什么", top_k=3)
        self.assertEqual(result["memories"][0]["content"], "用户喜欢简洁回答")
        self.assertEqual(result["trace"]["controller"], "fixture")
        snapshot = await self.worker.inspect(query="简洁")
        self.assertEqual(snapshot["nodes"][0]["type"], "EVENT")
        forgotten = await self.worker.forget([result["memories"][0]["id"]])
        self.assertEqual(forgotten["deleted"][0]["content"], "用户喜欢简洁回答")
        self.assertEqual(forgotten["memory_count"], 0)
        self.assertTrue((await self.worker.save())["saved"])

    async def test_input_validation_happens_before_dispatch(self):
        with self.assertRaisesRegex(ValueError, "非空列表"):
            await self.worker.remember([])
        with self.assertRaisesRegex(ValueError, "1 到 50"):
            await self.worker.query("测试", top_k=0)
        with self.assertRaisesRegex(ValueError, "1 到 50"):
            await self.worker.forget([])
        with self.assertRaisesRegex(ValueError, "1 到 200"):
            await self.worker.inspect(node_limit=0)
        self.assertIsNone(self.worker.process)

    async def test_worker_error_is_not_treated_as_success(self):
        with self.assertRaisesRegex(MemoryUnavailable, "expected failure"):
            await self.worker._request("fail")

    async def test_cancelled_request_resets_worker_protocol_stream(self):
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.worker._request("slow"), .05)
        self.assertIsNone(self.worker.process)
        self.assertEqual((await self.worker.health())["status"], "ready")

    def test_explicit_jev_backend_requires_key_before_start(self):
        worker = JevMemoryWorker(backend="jev", command=[sys.executable, "unused"])
        with self.assertRaisesRegex(MemoryUnavailable, "TYPESAFE_API_KEY"):
            worker._environment()

    def test_real_worker_forget_removes_graph_vector_and_keyword_entries(self):
        worker = Worker.__new__(Worker)
        worker.system = FakeSystem()
        result = worker.handle({"operation": "forget", "memory_ids": ["memory-1", "missing"]})
        self.assertEqual([item["id"] for item in result["deleted"]], ["memory-1"])
        self.assertEqual(result["missing"], ["missing"])
        self.assertNotIn("memory-1", worker.system.graph_db.nodes)
        self.assertNotIn("memory-1", worker.system.vector_db.ids)
        self.assertNotIn("简洁", worker.system.memory_builder.node_index)
        self.assertEqual(worker.system.memory_builder.node_index["其他"], {"memory-2"})
        self.assertTrue(worker.system.saved)
        self.assertEqual(worker.system.memory_builder.jev.audit.events[0][0], "memory_forgotten")

    def test_inspect_is_sanitized_limited_and_searchable(self):
        worker = Worker.__new__(Worker)
        worker.system = FakeSystem()
        result = worker.handle({"operation": "inspect", "query": "简洁",
                                "node_limit": 1, "edge_limit": 0})
        self.assertEqual([node["id"] for node in result["nodes"]], ["memory-1"])
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["statistics"]["node_count"], 2)
        self.assertEqual(result["statistics"]["edge_count"], 1)
        encoded = str(result)
        self.assertNotIn("raw_content", encoded)
        self.assertNotIn("private_score", encoded)
        self.assertNotIn("embedding", encoded)

    def test_inspect_selected_node_returns_only_one_hop_graph(self):
        worker = Worker.__new__(Worker)
        nodes = [FakeNode("a", "A"), FakeNode("b", "B"), FakeNode("c", "C")]
        links = [FakeLink("ab", "a", "b"), FakeLink("bc", "b", "c")]
        worker.system = type("System", (), {"graph_db": FakeGraph(nodes, links)})()
        result = worker.handle({"operation": "inspect", "selected_id": "a",
                                "node_limit": 10, "edge_limit": 10})
        self.assertEqual({node["id"] for node in result["nodes"]}, {"a", "b"})
        self.assertEqual([edge["id"] for edge in result["edges"]], ["ab"])
        self.assertEqual(result["selected_id"], "a")

    def test_inspect_empty_graph(self):
        worker = Worker.__new__(Worker)
        worker.system = type("System", (), {"graph_db": FakeGraph([])})()
        result = worker.handle({"operation": "inspect"})
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["statistics"]["node_count"], 0)


class AutomaticMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_interaction_is_durably_extracted_and_indexed(self):
        class Extractor:
            version = "test-v1"
            provider = "deepseek"
            model = "deepseek-flash"

            async def extract(self, *, target_messages, context_messages,
                              existing_memories):
                del context_messages, existing_memories
                user = next(item for item in target_messages if item.role == "user")
                return (MemoryCandidate(
                    content="用户喜欢爵士乐", subject="user", kind="preference",
                    durability="stable", operation="add", confidence=.96,
                    sensitivity="normal",
                    evidence=({"event_id": user.event_id,
                               "quote": "我喜欢爵士乐"},)),)

        class Backend:
            def __init__(self):
                self.observations = []

            async def remember(self, observations):
                self.observations.extend(observations)
                return {"admitted": len(observations), "rejected": 0}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            ledger = JsonMemoryLedger(root / "memory")
            backend = Backend()
            worker = MemoryExtractionCoordinator(
                sessions, ledger, Extractor(), backend)
            service = ConversationService(sessions, extraction_sink=worker)
            await worker.start()
            context = await service.begin_interaction("我喜欢爵士乐")
            await service.finish_interaction(
                context, status="succeeded", assistant_content="好呀")

            queued = await ledger.jobs(statuses={"pending", "running", "completed"})
            self.assertEqual(len(queued), 1)
            await worker.drain()
            jobs = await ledger.jobs()
            memories = await ledger.memories()

            self.assertEqual(jobs[0].status, "completed")
            self.assertEqual(memories[0].status, "active")
            self.assertEqual(memories[0].metadata["index_state"], "indexed")
            self.assertEqual(backend.observations[0]["content"], "用户喜欢爵士乐")
            self.assertEqual(
                backend.observations[0]["metadata"]["canonical_memory_id"],
                memories[0].memory_id)
            await worker.close()

    async def test_pending_job_is_resumed_after_coordinator_restart(self):
        class Extractor:
            version = "test-v1"
            provider = "deepseek"
            model = "deepseek-flash"

            async def extract(self, **_kwargs):
                return ()

        class Backend:
            async def remember(self, _observations):
                raise AssertionError("empty extraction must not index")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            service = ConversationService(sessions)
            context = await service.begin_interaction("你好")
            await service.finish_interaction(
                context, status="succeeded", assistant_content="你好呀")
            ledger = JsonMemoryLedger(root / "memory")
            await ledger.start()
            await ledger.enqueue_job(
                session_id=context.session.session_id,
                interaction_id=context.interaction_id,
                source_hash="source", extractor_version="test-v1")

            restarted = MemoryExtractionCoordinator(
                sessions, JsonMemoryLedger(root / "memory"),
                Extractor(), Backend())
            await restarted.start()
            await restarted.drain()

            jobs = await restarted.ledger.jobs()
            self.assertEqual(jobs[0].status, "skipped")
            self.assertEqual(jobs[0].attempts, 1)
            await restarted.close()

    async def test_stable_profile_and_local_recall_only_use_active_memories(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            active = CanonicalMemory(
                memory_id="mem_active", revision=1, content="用户喜欢爵士乐",
                subject="user", kind="preference", status="active",
                confidence=.9, sensitivity="normal", source_mode="automatic",
                source_session_id="ses_1", source_interaction_id="int_1",
                source_event_ids=("evt_1",), created_at=1, updated_at=1)
            pending = CanonicalMemory(
                memory_id="mem_pending", revision=1, content="用户的健康信息",
                subject="user", kind="profile", status="pending_review",
                confidence=.9, sensitivity="sensitive", source_mode="automatic",
                source_session_id="ses_1", source_interaction_id="int_1",
                source_event_ids=("evt_1",), created_at=2, updated_at=2)
            await ledger.save_memory(active)
            await ledger.save_memory(pending)
            provider = MemoryContextProvider(ledger)

            profile = await provider.stable_profile()
            evidence = await provider.recall("播放爵士乐", session_id="ses_2")

            self.assertIn("用户喜欢爵士乐", profile)
            self.assertNotIn("健康信息", profile)
            self.assertEqual(evidence[0]["memory_id"], "mem_active")

    async def test_canonical_snapshot_rebuilds_from_append_only_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "memory"
            ledger = JsonMemoryLedger(root)
            await ledger.start()
            record = CanonicalMemory(
                memory_id="mem_1", revision=1, content="用户喜欢爵士乐",
                subject="user", kind="preference", status="active",
                confidence=.9, sensitivity="normal", source_mode="automatic",
                source_session_id="ses_1", source_interaction_id="int_1",
                source_event_ids=("evt_1",), created_at=1, updated_at=1)
            await ledger.save_memory(record)
            (root / "ledger/snapshot.json").unlink()

            reopened = JsonMemoryLedger(root)
            await reopened.start()

            self.assertEqual((await reopened.memories())[0].memory_id, "mem_1")

    def test_secret_redaction_and_policy_are_deterministic(self):
        secret = "我的密码: example-only-not-real"
        self.assertTrue(contains_secret(secret))
        self.assertNotIn("example-only-not-real", redact_memory_source(secret))
        candidate = MemoryCandidate(
            content=secret, subject="user", kind="preference",
            durability="stable", operation="add", confidence=.99,
            sensitivity="normal", evidence=({"event_id": "evt", "quote": secret},))
        self.assertEqual(memory_admission(candidate), "rejected")

    async def test_explicit_memory_also_enters_canonical_ledger(self):
        class Backend:
            async def remember(self, observations):
                return {"admitted": 1, "rejected": 0,
                        "created": [{"id": "jev-1",
                                     "content": observations[0]["content"]}],
                        "memory_count": 1}

        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            service = MemoryService(Backend(), ledger)

            result = await service.remember(
                "用户喜欢爵士乐", source="voice_explicit",
                session_id="ses_1", interaction_id="int_1",
                source_event_ids=("evt_1",))
            records = await ledger.memories()

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(records[0].status, "active")
            self.assertEqual(records[0].backend_links, ("jev-1",))
            self.assertEqual(records[0].source_event_ids, ("evt_1",))

    async def test_explicit_memory_returns_after_durable_commit_and_enqueues_index(self):
        class Backend:
            async def remember(self, _observations):
                raise AssertionError("foreground remember must not wait for index")

        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            queued = []
            service = MemoryService(Backend(), ledger, index_enqueue=queued.append)

            result = await service.remember("用户喜欢简洁回答")
            record = (await ledger.memories())[0]

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(record.status, "active")
            self.assertEqual(record.metadata["index_state"], "pending")
            self.assertEqual(queued, [record.memory_id])

    async def test_user_recall_falls_back_to_canonical_and_delete_maps_backend_id(self):
        class Backend:
            def __init__(self):
                self.forgotten = []

            async def query(self, *_args, **_kwargs):
                raise RuntimeError("jev unavailable")

            async def forget(self, memory_ids):
                self.forgotten.extend(memory_ids)
                return {"deleted": [{"id": item, "content": "用户喜欢爵士乐",
                                     "timestamp": None} for item in memory_ids],
                        "missing": [], "memory_count": 0}

        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            await ledger.save_memory(CanonicalMemory(
                memory_id="mem_1", revision=1, content="用户喜欢爵士乐",
                subject="user", kind="preference", status="active",
                confidence=.9, sensitivity="normal", source_mode="automatic",
                source_session_id="ses_1", source_interaction_id="int_1",
                source_event_ids=("evt_1",), created_at=1, updated_at=1,
                backend_links=("jev-1",)))
            backend = Backend()
            service = MemoryService(backend, ledger)

            recalled = await service.recall("爵士乐")
            forgotten = await service.forget([recalled["memories"][0]["id"]])
            record = (await ledger.memories())[0]

            self.assertEqual(recalled["memories"][0]["id"], "mem_1")
            self.assertEqual(backend.forgotten, ["jev-1"])
            self.assertEqual(forgotten["status"], "succeeded")
            self.assertEqual(record.status, "deleted")

    async def test_semantic_slot_update_supersedes_old_preference_and_preserves_lineage(self):
        class Backend:
            def __init__(self):
                self.created = 0
                self.forgotten = []

            async def remember(self, observations):
                self.created += 1
                return {"admitted": 1, "created": [{"id": f"jev-{self.created}"}]}

            async def forget(self, memory_ids):
                self.forgotten.extend(memory_ids)
                return {"deleted": [], "missing": [], "memory_count": 0}

        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            backend = Backend()
            service = MemoryService(backend, ledger)
            first = MemoryCandidate(
                content="用户喜欢爵士乐", subject="user", kind="preference",
                durability="stable", operation="add", confidence=.95,
                sensitivity="normal", canonical_slot="preference:music_genre",
                evidence=({"event_id": "evt_1", "quote": "喜欢爵士乐"},))
            correction = MemoryCandidate(
                content="用户现在更喜欢古典音乐", subject="user", kind="preference",
                durability="stable", operation="update", confidence=.97,
                sensitivity="normal", canonical_slot="preference:music_genre",
                evidence=({"event_id": "evt_2", "quote": "更喜欢古典音乐"},))

            old = (await service.admit_candidate(
                first, source_mode="automatic", session_id="ses_1",
                interaction_id="int_1", source_event_ids=("evt_1",)))["memory"]
            new = (await service.admit_candidate(
                correction, source_mode="automatic", session_id="ses_1",
                interaction_id="int_2", source_event_ids=("evt_2",)))["memory"]
            records = await ledger.memories()

            self.assertEqual((await ledger.memory(old.memory_id)).status, "superseded")
            self.assertEqual(new.status, "active")
            self.assertEqual(new.supersedes_revision, old.revision)
            self.assertEqual(new.lineage_event_ids, ("evt_1", "evt_2"))
            self.assertEqual(backend.forgotten, ["jev-1"])
            self.assertEqual([item.content for item in records if item.status == "active"],
                             ["用户现在更喜欢古典音乐"])

    async def test_pending_review_requires_approval_before_indexing(self):
        class Backend:
            def __init__(self):
                self.observations = []

            async def remember(self, observations):
                self.observations.extend(observations)
                return {"admitted": 1, "created": [{"id": "jev-approved"}]}

        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            backend = Backend()
            service = MemoryService(backend, ledger)
            candidate = MemoryCandidate(
                content="用户有一项需要确认的健康偏好", subject="user", kind="profile",
                durability="stable", operation="add", confidence=.91,
                sensitivity="sensitive", canonical_slot="profile:health_preference",
                evidence=({"event_id": "evt_1", "quote": "健康偏好"},))
            pending = (await service.admit_candidate(
                candidate, source_mode="automatic", session_id="ses_1",
                interaction_id="int_1", source_event_ids=("evt_1",)))["memory"]

            self.assertEqual(pending.status, "pending_review")
            self.assertEqual(backend.observations, [])
            self.assertEqual(len(await service.pending_review()), 1)

            approved = await service.approve(pending.memory_id)
            self.assertEqual(approved["memory"]["status"], "active")
            self.assertEqual(backend.observations[0]["content"], candidate.content)

    async def test_index_failure_retries_durably_until_success(self):
        class Extractor:
            version = "retry-v1"

            async def extract(self, *, target_messages, **_kwargs):
                user = next(item for item in target_messages if item.role == "user")
                return (MemoryCandidate(
                    content="用户喜欢摇滚乐", subject="user", kind="preference",
                    durability="stable", operation="add", confidence=.95,
                    sensitivity="normal", canonical_slot="preference:music_genre",
                    evidence=({"event_id": user.event_id, "quote": "喜欢摇滚乐"},)),)

        class Backend:
            def __init__(self):
                self.attempts = 0

            async def remember(self, _observations):
                self.attempts += 1
                if self.attempts < 3:
                    raise RuntimeError("temporary")
                return {"admitted": 1, "created": [{"id": "jev-3"}]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            ledger = JsonMemoryLedger(root / "memory")
            backend = Backend()
            worker = MemoryExtractionCoordinator(
                sessions, ledger, Extractor(), backend,
                retry_base_seconds=.01)
            service = ConversationService(sessions, extraction_sink=worker)
            await worker.start()
            context = await service.begin_interaction("我喜欢摇滚乐")
            await service.finish_interaction(
                context, status="succeeded", assistant_content="知道啦")
            await worker.drain()

            memory = (await ledger.memories())[0]
            self.assertEqual(memory.status, "active")
            self.assertEqual(memory.metadata["index_attempts"], 3)
            self.assertEqual(memory.backend_links, ("jev-3",))
            await worker.close()

    async def test_extraction_failure_retries_with_persisted_backoff_state(self):
        class Extractor:
            version = "retry-extractor-v1"

            def __init__(self):
                self.attempts = 0

            async def extract(self, **_kwargs):
                self.attempts += 1
                if self.attempts < 3:
                    raise RuntimeError("temporary extractor outage")
                return ()

        class Backend:
            async def remember(self, _observations):
                raise AssertionError("empty extraction must not index")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = JsonlSessionStore(root / "conversations")
            ledger = JsonMemoryLedger(root / "memory")
            extractor = Extractor()
            worker = MemoryExtractionCoordinator(
                sessions, ledger, extractor, Backend(),
                retry_base_seconds=.01)
            service = ConversationService(sessions, extraction_sink=worker)
            await worker.start()
            context = await service.begin_interaction("你好，我今天想聊聊天")
            await service.finish_interaction(
                context, status="succeeded", assistant_content="好呀")
            await worker.drain()

            job = (await ledger.jobs())[0]
            self.assertEqual(job.status, "skipped")
            self.assertEqual(job.attempts, 3)
            self.assertIsNone(job.next_retry_at)
            history = (root / "memory/extraction/jobs.jsonl").read_text()
            self.assertIn('"status":"retry_wait"', history)
            await worker.close()

    async def test_stable_profile_snapshot_is_atomic_and_deleted_memory_disappears(self):
        class Backend:
            async def remember(self, _observations):
                return {"admitted": 1, "created": [{"id": "jev-1"}]}

            async def forget(self, memory_ids):
                return {"deleted": [{"id": item} for item in memory_ids]}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = JsonMemoryLedger(root / "memory")
            await ledger.start()
            provider = MemoryContextProvider(
                ledger, profile_path=root / "stable-profile.json")
            service = MemoryService(
                Backend(), ledger,
                projection_callback=provider.refresh_stable_profile)
            result = await service.remember("用户喜欢简洁回答")
            packet = json.loads((root / "stable-profile.json").read_text())
            self.assertIn("用户喜欢简洁回答", packet["context"])

            await service.delete_node(result["memory_id"])
            packet = json.loads((root / "stable-profile.json").read_text())
            self.assertEqual(packet["memory_ids"], [])
            self.assertEqual(packet["context"], "")

    async def test_delete_tombstone_survives_backend_cleanup_failure(self):
        class Backend:
            async def remember(self, _observations):
                return {"admitted": 1, "created": [{"id": "jev-1"}]}

            async def forget(self, _memory_ids):
                raise RuntimeError("backend offline")

        with tempfile.TemporaryDirectory() as directory:
            ledger = JsonMemoryLedger(Path(directory) / "memory")
            await ledger.start()
            service = MemoryService(Backend(), ledger)
            remembered = await service.remember("用户喜欢爵士乐")

            deleted = await service.delete_node(remembered["memory_id"])
            record = await ledger.memory(remembered["memory_id"])

            self.assertEqual(deleted["status"], "succeeded")
            self.assertEqual(record.status, "deleted")
            self.assertEqual(record.metadata["index_cleanup_state"], "failed")
            self.assertEqual(record.metadata["index_cleanup_error"], "RuntimeError")
