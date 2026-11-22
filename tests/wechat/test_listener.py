from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from boxagent.application.wechat.monitor import WeChatMonitor
from boxagent.domain.events.models import EventEnvelope, EventReceipt
from boxagent.infrastructure.wechat.database.snapshot import signature
from boxagent.infrastructure.wechat.listener import DatabaseMessageSource, JsonlEventSink

from .conftest import PlainSnapshots, add_message


def test_source_baseline_restarts_and_no_duplicate_ack_update(
    snapshots: PlainSnapshots, tmp_path: Path
) -> None:
    add_message(snapshots, 1)
    directory = tmp_path / "source"
    source = DatabaseMessageSource(snapshots.repository(), directory)
    assert source.poll() == 0
    add_message(snapshots, 2, "新消息")
    add_message(snapshots, 3, "我发出的", sender=1)
    assert source.poll() == 1
    item = source.pending()[0]
    assert item.event.source == "wechat" and item.event.content == "新消息"
    assert item.event.account_id == "wechat:fixture"
    source.close()
    resumed = DatabaseMessageSource(snapshots.repository(), directory)
    assert resumed.pending()[0].event.event_id == item.event.event_id
    resumed.acknowledge(item.event.event_id, accepted=True)
    assert resumed.poll() == 0 and resumed.pending() == ()
    resumed.close()


def test_single_source_lease(snapshots: PlainSnapshots, tmp_path: Path) -> None:
    source = DatabaseMessageSource(snapshots.repository(), tmp_path / "state")
    with pytest.raises(BlockingIOError):
        DatabaseMessageSource(snapshots.repository(), tmp_path / "state")
    source.close()


def test_call_metadata_is_replaced_before_inbox_handoff(
    snapshots: PlainSnapshots, tmp_path: Path
) -> None:
    source = DatabaseMessageSource(snapshots.repository(), tmp_path / "source")
    try:
        assert source.poll() == 0
        add_message(snapshots, 1, "native call metadata", local_type=(2 << 32) | 50)
        add_message(snapshots, 2, "电话结束后聊", local_type=1)
        assert source.poll() == 2
        assert [item.event.content for item in source.pending()] == ["[音视频通话]", "电话结束后聊"]
    finally:
        source.close()


def test_reader_refresh_cannot_consume_listener_change(
    snapshots: PlainSnapshots, tmp_path: Path
) -> None:
    source = DatabaseMessageSource(snapshots.repository(), tmp_path / "source")
    assert source.poll() == 0
    add_message(snapshots, 1)
    database = snapshots.source("message/message_0.db")
    database.last_signature = signature(
        database.path
    )  # A concurrent query refreshed the shared cache.
    assert source.poll() == 1
    source.close()


def test_monitor_acknowledges_only_matching_durable_receipt(
    snapshots: PlainSnapshots, tmp_path: Path
) -> None:
    source = DatabaseMessageSource(snapshots.repository(), tmp_path / "source")

    class Sink:
        good = False
        async def accept(self, event: EventEnvelope) -> EventReceipt:
            return EventReceipt(event.event_id if self.good else "wrong-id", True)

    sink = Sink()
    monitor = WeChatMonitor(lambda: source, sink)

    async def exercise() -> None:
        await monitor.poll_once()
        add_message(snapshots, 1)
        await monitor.poll_once()
        assert source._state.counts()["pending"] == 1
        sink.good = True
        source._state.connection.execute("UPDATE events SET retry_at=0")
        source._state.connection.commit()
        await monitor.poll_once()
        assert source._state.counts()["pending"] == 0
        await monitor.close()

    asyncio.run(exercise())


def test_jsonl_sink_idempotency_survives_recreation(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    event = EventEnvelope("one", "wechat", "account", "message.received", "peer", 1, 2, "hello")
    asyncio.run(JsonlEventSink(path).accept(event))
    asyncio.run(JsonlEventSink(path).accept(event))
    assert len(path.read_text().splitlines()) == 1


def test_monitor_closes_after_thread_poll(snapshots: PlainSnapshots, tmp_path: Path) -> None:
    source = DatabaseMessageSource(snapshots.repository(), tmp_path / "source")
    monitor = WeChatMonitor(lambda: source, JsonlEventSink(tmp_path / "events.jsonl"), interval=0.1)

    async def exercise() -> None:
        await monitor.start()
        assert monitor.running
        await monitor.close()
        assert not monitor.running
        with pytest.raises(RuntimeError, match="closed"):
            source.poll()

    asyncio.run(exercise())
