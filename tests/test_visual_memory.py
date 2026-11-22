"""Source identity, real graph roundtrips and the asynchronous memory boundary."""

import asyncio
from dataclasses import replace
import hashlib
import io
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image
import pytest

from boxagent.application.memory import MemoryModule
from boxagent.application.visual_memory import VisualMemoryCoordinator
from boxagent.domain.memory.service import MemoryService
from boxagent.domain.memory.visual import (
    EpisodeAtom, VisualEpisode, VisualEvidence, VisualFrame, requests_visual_memory,
)
from boxagent.infrastructure.memory.visual_episode import store_visual_episode
from boxagent.infrastructure.memory.visual_extractor import GroundedVisualEpisodeExtractor
from boxagent.infrastructure.memory.jev_mem.core.graph_db import EventNode, EpisodeNode, NetworkXGraphDB
from boxagent.infrastructure.memory.jev_mem.worker import node_payload, Worker
from boxagent.infrastructure.perception.qwen_mlx import WindowSummary
from boxagent.infrastructure.persistence.visual_evidence import LocalVisualEvidenceStore


def png(color="white"):
    buffer = io.BytesIO()
    Image.new("RGB", (180, 100), color).save(buffer, "PNG")
    return buffer.getvalue()


def evidence(identity="one", *, session="session", timestamp=None, image=None):
    image = image or png()
    timestamp = timestamp or time.time()
    return VisualEvidence(identity, session, "turn", "event-" + identity,
                          "记住《鸣潮》无冠者这套配队", "鸣潮无冠者配队：角色甲、角色乙。武器：测试剑。",
                          timestamp, timestamp, "鸣潮", 7, hashlib.sha256(image).hexdigest())


def episode(source):
    return VisualEpisode(source, (EpisodeAtom(source.caption, "visual_observation", source.caption),),
                         game="鸣潮", subject="无冠者", aspect="lineup")


class GraphBackend:
    """Real vendored graph/storage, with deterministic embedding/model boundaries."""
    def __init__(self, path):
        self.path = path
        self.graph_db = NetworkXGraphDB()
        if path.exists():
            self.graph_db.load(str(path))
        self.memory_builder = SimpleNamespace(_build_magma=self._build)
        self.remember_calls = 0

    def _build(self, text, *, timestamp, metadata):
        node = EventNode(content_narrative=text, timestamp=timestamp, attributes=metadata)
        self.graph_db.add_node(node)
        return node

    def save_memory(self):
        self.graph_db.save(str(self.path))

    async def remember_episode(self, payload):
        self.remember_calls += 1
        return store_visual_episode(self, payload)

    async def health(self):
        return {"status": "ready"}

    async def query(self, query, *, top_k=5, mode="deep"):
        nodes = sorted(self.graph_db.nodes.values(),
                       key=lambda node: getattr(node, "start_timestamp", None) or getattr(node, "timestamp", None), reverse=True)
        return {"memories": [node_payload(node) for node in nodes[:top_k]]}

    async def forget(self, ids):
        deleted = []
        for identity in ids:
            node = self.graph_db.get_node(identity)
            if node:
                deleted.append(node_payload(node))
                self.graph_db.delete_node(identity)
        self.save_memory()
        return {"deleted": deleted, "missing": []}

    async def forget_episode(self, evidence_id):
        ids = [node.node_id for node in self.graph_db.nodes.values()
               if node.attributes.get("evidence_id") == evidence_id]
        return {**await self.forget(ids), "memory_ids": ids}

    async def close(self):
        pass


class Source:
    def __init__(self):
        self.frame = VisualFrame(png(), evidence().caption, time.time(), "鸣潮", 7)
        self.describe = AsyncMock(return_value="当时画面中的武器是测试剑。")

    def current_frame(self, *, requested_at):
        return self.frame


class Extractor:
    async def extract(self, source):
        return episode(source)


def setup(tmp_path):
    backend = GraphBackend(tmp_path / "graph.json")
    source = Source()
    store = LocalVisualEvidenceStore(tmp_path / "media")
    coordinator = VisualMemoryCoordinator(backend, store, Extractor(), source)
    module = MemoryModule(MemoryService(backend))
    module.configure_visual(coordinator)
    return module, coordinator, backend, source, store


async def remember(module, identity="one", session="session"):
    return await module.remember_screen(user_request=evidence().user_request, session_id=session,
                                        interaction_id="turn", source_event_id=identity,
                                        requested_at=time.time())


def test_atoms_cannot_invent_facts_or_versions():
    payload = episode(evidence()).payload()
    payload["atoms"][0]["text"] = "用户已经通关且连续失败三次"
    with pytest.raises(ValueError, match="扩写"):
        VisualEpisode.from_payload(payload)
    payload = episode(evidence()).payload()
    payload["game_version"] = "9.99"
    with pytest.raises(ValueError, match="来源"):
        VisualEpisode.from_payload(payload)
    payload = episode(evidence()).payload()
    payload["atoms"][0]["kind"] = "user_report"
    with pytest.raises(ValueError, match="原文"):
        VisualEpisode.from_payload(payload)


@pytest.mark.parametrize("text", ["记住这个偏好，我喜欢红色", "不要记住这套配队", "回忆之前保存的截图", "忘记这张截图", "don't save this image"])
def test_visual_retention_is_explicit_and_not_a_negation(text):
    assert not requests_visual_memory(text)


@pytest.mark.parametrize("text", ["记住昨天那张截图", "保存上次的配队画面", "save yesterday's image"])
def test_past_image_request_does_not_save_current_frame(tmp_path, text):
    async def run():
        module, coordinator, backend, source, store = setup(tmp_path)
        result = await module.remember_screen(user_request=text, session_id="session", interaction_id="turn",
                                              source_event_id="past-image", requested_at=time.time())
        assert result["status"] == "clarification_required"
        assert not await store.pending()
        assert backend.remember_calls == 0
        await module.close()
    asyncio.run(run())


def test_notification_failure_does_not_lose_committed_memory(tmp_path):
    async def run():
        module, coordinator, backend, source, store = setup(tmp_path)
        coordinator.completed = AsyncMock(side_effect=OSError("notification unavailable"))
        result = await remember(module)
        await coordinator.drain()
        ids = await store.memory_ids(result["evidence_id"])
        assert len(ids) == 2
        assert (await module.visual_evidence(ids[0]))["status"] == "succeeded"
        assert not await store.pending()
        await module.close()
    asyncio.run(run())


def test_graph_episode_roundtrip_chain_and_idempotent_retry(tmp_path):
    backend = GraphBackend(tmp_path / "graph.json")
    late = episode(evidence("late", timestamp=300))
    early = episode(evidence("early", timestamp=100))
    middle = episode(evidence("middle", timestamp=200))
    receipts = [store_visual_episode(backend, item.payload()) for item in (late, early, middle)]
    count = len(backend.graph_db.nodes)
    assert store_visual_episode(backend, middle.payload())["memory_ids"] == receipts[-1]["memory_ids"]
    assert len(backend.graph_db.nodes) == count
    restored = GraphBackend(backend.path)
    cells = [node for node in restored.graph_db.nodes.values() if isinstance(node, EpisodeNode)]
    assert len(cells) == 3 and all(cell.summary and cell.event_node_ids for cell in cells)
    edges = [edge for edge in restored.graph_db.links.values() if edge.properties.get("visual_chain")]
    assert len(edges) == 2
    assert {(edge.source_node_id, edge.target_node_id) for edge in edges} == {
        (receipts[1]["episode_id"], receipts[2]["episode_id"]),
        (receipts[2]["episode_id"], receipts[0]["episode_id"]),
    }
    other_aspect = replace(episode(evidence("weapon", timestamp=400)), aspect="equipment")
    store_visual_episode(restored, other_aspect.payload())
    assert len([edge for edge in restored.graph_db.links.values() if edge.properties.get("visual_chain")]) == 2


def test_worker_protocol_uses_real_episode_writer(tmp_path):
    worker = Worker.__new__(Worker)
    worker.system = GraphBackend(tmp_path / "graph.json")
    result = worker.handle({"operation": "remember_episode", "episode": episode(evidence()).payload()})
    assert result["atom_count"] == 1
    cell = worker.system.graph_db.get_node(result["episode_id"])
    assert isinstance(cell, EpisodeNode)
    assert cell.attributes["source_event_id"] == "event-one"


@pytest.mark.parametrize("pending_is_anchor", [False, True])
def test_interrupted_visual_write_cannot_leak_through_recall_summary(tmp_path, pending_is_anchor):
    worker = Worker.__new__(Worker)
    worker.system = GraphBackend(tmp_path / "graph.json")
    published = EventNode(content_narrative="已经确认的用户事实")
    pending = EventNode(content_narrative="未提交的画面观察", attributes={"source_episode_status": "pending"})
    for node in (published, pending):
        worker.system.graph_db.add_node(node)
    context = SimpleNamespace(anchor_nodes=[published, pending] if pending_is_anchor else [published], metadata={})
    worker.system.query_engine = SimpleNamespace(query=lambda _question, **_kwargs: (context, "含有未提交画面邻居的摘要"))
    result = worker.handle({"operation": "query", "question": "过去的经历"})
    assert [row["id"] for row in result["memories"]] == [published.node_id]
    assert result["evidence"] == ""


def test_background_source_binding_cross_session_and_rereading(tmp_path):
    async def run():
        module, coordinator, backend, source, store = setup(tmp_path)
        receipt = await remember(module)
        assert receipt["status"] == "queued"
        await module.await_idle()
        answer = await module.recall("上次鸣潮用什么队")
        identity = answer["memories"][0]["id"]
        preview = await module.visual_evidence(identity)
        assert preview["source_event_id"] == "one"
        assert preview["image"].startswith("data:image/png;base64,")
        looked = await module.look_at_source(identity, "上次截图里武器是什么")
        assert looked["observation"] == "当时画面中的武器是测试剑。"
        source.describe.assert_awaited_once()
        assert backend.remember_calls == 1
        # A new facade/process can find the same source without any current frame.
        second, _, _, second_source, _ = setup(tmp_path)
        second_source.frame = None
        restored = await second.recall("上次的配队")
        assert (await second.visual_evidence(restored["memories"][0]["id"]))["source_event_id"] == "one"
        await module.close()
        await second.close()
    asyncio.run(run())


def test_same_image_in_distinct_messages_keeps_distinct_provenance(tmp_path):
    async def run():
        module, _, backend, _, store = setup(tmp_path)
        first = await remember(module, "one", "session-a")
        second = await remember(module, "two", "session-b")
        await module.await_idle()
        assert first["evidence_id"] != second["evidence_id"]
        assert len(backend.graph_db.nodes) == 4
        assert len(list(store.root.glob("*.png"))) == 1
        await module.close()
    asyncio.run(run())


def test_pending_receipt_recovers_after_shutdown_without_capture(tmp_path):
    async def run():
        module, coordinator, _, _, store = setup(tmp_path)
        gate = asyncio.Event()
        async def blocked(source):
            await gate.wait()
            return episode(source)
        coordinator.extractor = SimpleNamespace(extract=blocked)
        receipt = await remember(module)
        await asyncio.sleep(0)
        await module.close()
        assert await store.pending()
        second, _, backend, source, _ = setup(tmp_path)
        source.frame = None
        await second.start()
        await second.await_idle()
        assert backend.remember_calls == 1
        assert await store.memory_ids(receipt["evidence_id"])
        await second.close()
    asyncio.run(run())


def test_delete_cascades_atoms_and_shared_media_and_blocks_resurrection(tmp_path):
    async def run():
        module, _, backend, _, store = setup(tmp_path)
        first = await remember(module, "one")
        second = await remember(module, "two")
        await module.await_idle()
        first_ids = await store.memory_ids(first["evidence_id"])
        old_evidence = await store.for_memory(first_ids[0])
        await module.delete_node(first_ids[-1])
        assert not any(identity in backend.graph_db.nodes for identity in first_ids)
        assert len(list(store.root.glob("*.png"))) == 1
        with pytest.raises(ValueError, match="重新写入"):
            await store.put(old_evidence, png())
        second_ids = await store.memory_ids(second["evidence_id"])
        await module.recall("配队")
        await module.forget([second_ids[0]])
        assert not backend.graph_db.nodes
        assert not list(store.root.glob("*.png"))
        assert (await module.visual_evidence(second_ids[0]))["status"] == "not_found"
        await module.close()
    asyncio.run(run())


def test_tampered_source_and_arbitrary_ids_cannot_be_opened(tmp_path):
    async def run():
        module, _, _, _, store = setup(tmp_path)
        receipt = await remember(module)
        await module.await_idle()
        identity = (await store.memory_ids(receipt["evidence_id"]))[0]
        assert (await module.visual_evidence("../../secret"))["status"] == "not_found"
        path = next(store.root.glob("*.png"))
        path.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="校验"):
            await module.visual_evidence(identity)
        await module.close()
    asyncio.run(run())


def test_missing_frame_never_claims_saved(tmp_path):
    async def run():
        module, _, backend, source, store = setup(tmp_path)
        source.frame = None
        assert (await remember(module))["status"] == "clarification_required"
        assert not await store.pending() and backend.remember_calls == 0
        await module.close()
    asyncio.run(run())


def test_current_frame_requires_matching_window_and_time():
    observer = WindowSummary(Mock())
    now = time.time()
    observer.latest_frame = VisualFrame(png(), "配队", now, "鸣潮", 7)
    with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window", return_value={"id": 7, "app": "鸣潮"}):
        assert observer.current_frame(requested_at=now) is not None
        assert observer.current_frame(requested_at=now - 5) is None
    with patch("boxagent.infrastructure.perception.qwen_mlx.focused_window", return_value={"id": 8, "app": "鸣潮"}):
        assert observer.current_frame(requested_at=now) is None


def test_invalid_extraction_keeps_only_observed_quote():
    async def run():
        extractor = GroundedVisualEpisodeExtractor(api_key="fixture", base_url="https://example.invalid", model="fixture")
        with patch.object(extractor, "_request", return_value={"atoms": [{"kind": "user_report", "quote": "用户通关了"}]}):
            result = await extractor.extract(evidence())
        assert result.atoms[0].kind == "visual_observation"
        assert "用户通关了" not in result.summary
        assert not result.game_version
    asyncio.run(run())


def test_deleted_pending_delivery_cannot_be_recreated(tmp_path):
    async def run():
        module, coordinator, backend, _, store = setup(tmp_path)
        source = evidence("pending")
        await store.put(source, png())
        # Simulate a graph write whose source receipt was not acknowledged.
        ids = store_visual_episode(backend, episode(source).payload())["memory_ids"]
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(value):
            entered.set()
            await release.wait()
            return episode(value)
        coordinator.extractor = SimpleNamespace(extract=delayed)
        await module.start()
        await entered.wait()
        result = await module.delete_node(ids[0])
        assert set(result["deleted_ids"]) == set(ids)
        release.set()
        await module.await_idle()
        assert not backend.graph_db.nodes
        assert not list(store.root.glob("*.png"))
        assert not await store.pending()
        await module.close()
    asyncio.run(run())


def test_retry_reuses_durable_compilation_and_preserves_atom_identity(tmp_path):
    async def run():
        module, coordinator, backend, _, _ = setup(tmp_path)
        original = backend.remember_episode
        calls = 0
        async def uncertain(payload):
            nonlocal calls
            calls += 1
            result = await original(payload)
            if calls == 1:
                raise RuntimeError("lost acknowledgement")
            return result
        backend.remember_episode = uncertain
        extractor = AsyncMock(side_effect=lambda value: episode(value))
        coordinator.extractor = SimpleNamespace(extract=extractor)
        await remember(module)
        await module.await_idle()
        assert calls == 2 and extractor.await_count == 1
        assert len(backend.graph_db.nodes) == 2
        await module.close()
    asyncio.run(run())


def test_delete_journal_finishes_after_restart(tmp_path):
    async def run():
        module, coordinator, backend, _, store = setup(tmp_path)
        receipt = await remember(module)
        await module.await_idle()
        ids = await store.memory_ids(receipt["evidence_id"])
        backend.forget_episode = AsyncMock(side_effect=RuntimeError("worker unavailable"))
        with pytest.raises(RuntimeError):
            await module.delete_node(ids[0])
        assert await store.deleting()
        assert (await module.visual_evidence(ids[1]))["status"] == "not_found"
        await module.close()
        recovered, _, recovered_backend, _, _ = setup(tmp_path)
        await recovered.start()
        assert not recovered_backend.graph_db.nodes and not list(store.root.glob("*.png"))
        assert not await store.deleting()
        await recovered.close()
    asyncio.run(run())


def test_automatic_user_observation_does_not_deduplicate_into_image(tmp_path):
    from datetime import datetime, timezone
    worker = Worker.__new__(Worker)
    worker.system = GraphBackend(tmp_path / "graph.json")
    store_visual_episode(worker.system, episode(evidence()).payload())
    def build(observations):
        for observation in observations:
            worker.system._build(observation["content"], timestamp=datetime.now(timezone.utc),
                                 metadata=observation["metadata"])
        return {"admitted": len(observations), "rejected": 0}
    worker.system.build_memory_from_conversation = build
    result = worker.handle({"operation": "remember", "observations": [{
        "content": "我喜欢简短播报", "metadata": {"source": "automatic", "role": "user", "source_event_id": "event-one"},
    }]})
    assert result["deduplicated"] == 0
    assert result["created"][0]["metadata"]["role"] == "user"
