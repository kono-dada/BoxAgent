import fcntl
import asyncio
from functools import wraps
import json
from pathlib import Path

import pytest

from boxagent.bootstrap.session_migration import migrate_sessions, rollback_sessions
from boxagent.domain.conversation.models import ProductEvent, RuntimeBinding
from boxagent.infrastructure.persistence.jsonl_session_repository import JsonlSessionStore
from boxagent.infrastructure.persistence.session_events import SessionEventStore
from boxagent.infrastructure.persistence.unified_session import (
    UnifiedContextLedger, UnifiedSessionRepository,
)


def run_async(function):
    @wraps(function)
    def run(tmp_path):
        return asyncio.run(function(tmp_path))
    return run


def message(sid: str, eid: str, at: float = 1) -> ProductEvent:
    return ProductEvent(0, eid, "message.final", sid, at, role="user", content=eid)


def test_session_lock_tail_repair_and_corruption(tmp_path: Path):
    first, second = SessionEventStore(tmp_path), SessionEventStore(tmp_path)
    sid = "ses_test"
    try:
        first.append(sid, "turn/start", {}, dedup_key="one")
        with pytest.raises(RuntimeError):
            second.append(sid, "turn/end", {})
        path = first.directory(sid) / "session.v1.jsonl"
        with path.open("a") as stream:
            stream.write('{"seq":2')
        assert len(first.read(sid)) == 1
        assert first.append(sid, "turn/start", {}, dedup_key="one").seq == 1
        assert first.append(sid, "turn/end", {}).seq == 2
        first.close()
        assert second.append(sid, "turn/start", {}).seq == 3
        lines = path.read_text().splitlines()
        lines[1] = "broken"
        path.write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError):
            second.read(sid)
    finally:
        first.close()
        second.close()


def test_invalid_event_does_not_poison_store(tmp_path: Path):
    store = SessionEventStore(tmp_path)
    try:
        for kind, data, at in [("", {}, 1), ("message", {}, float("nan")),
                               ("message", {"x": float("inf")}, 1)]:
            with pytest.raises(ValueError):
                store.append("ses_test", kind, data, at=at)
        assert store.append("ses_test", "valid", {}, at=1).seq == 1
        assert len(store.read("ses_test")) == 1
    finally:
        store.close()


@run_async
async def test_archive_compression_replays_and_can_resume_writes(tmp_path: Path):
    events = SessionEventStore(tmp_path)
    repository = UnifiedSessionRepository(events)
    try:
        await repository.start()
        meta = await repository.create_session()
        await repository.append_event(message(meta.session_id, "before"))
        await repository.archive_session(meta.session_id)
        assert (events.directory(meta.session_id) / "session.v1.jsonl.gz").exists()
        assert not (events.directory(meta.session_id) / "session.v1.jsonl").exists()
        assert (await repository.read_events(meta.session_id))[0].content == "before"
        events.append(meta.session_id, "audit/recovered", {})
        assert len(events.read(meta.session_id)) == 2
    finally:
        await repository.close()


def test_attachment_dedup_and_integrity(tmp_path: Path):
    from boxagent.infrastructure.persistence.attachments import AttachmentStore
    store = AttachmentStore(tmp_path)
    first = store.put(b"image", "image/png")
    assert store.put(b"image", "image/png") == first
    assert len(list(tmp_path.rglob("*.png"))) == 1
    Path(first["path"]).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="哈希"):
        store.put(b"image", "image/png")


@run_async
async def test_interleaved_projections_preserve_product_cursor(tmp_path: Path):
    events = SessionEventStore(tmp_path)
    repository = UnifiedSessionRepository(events)
    try:
        await repository.start()
        session = await repository.create_session()
        ledger = UnifiedContextLedger(events)
        a = await repository.append_event(message(session.session_id, "a", 2))
        ledger.append(session.session_id, "system/message", {"content": "profile"})
        b = await repository.append_event(message(session.session_id, "b", 1))
        assert (a.sequence, b.sequence) == (1, 2)
        assert [e.seq for e in events.read(session.session_id)] == [1, 2, 3]
        assert [e.content for e in await repository.read_events(session.session_id, after_sequence=1)] == ["b"]
        assert (await repository.append_event(a)).sequence == 1
        binding = RuntimeBinding("codex", "codex", "m", "thread", 2)
        await repository.save_runtime_binding(session.session_id, binding)
        assert await repository.runtime_binding(session.session_id, "codex") == binding
    finally:
        await repository.close()


@run_async
async def test_migration_idempotent_and_rollback_keeps_new_activity(tmp_path: Path):
    old = JsonlSessionStore(tmp_path / "conversations")
    await old.start()
    session = await old.create_session("历史")
    sid = session.session_id
    await old.append_event(message(sid, "old", 3))
    before = (old.sessions_path / sid / "events.jsonl").read_bytes()
    await migrate_sessions(tmp_path)
    await migrate_sessions(tmp_path)
    assert (old.sessions_path / sid / "events.jsonl").read_bytes() == before
    events = SessionEventStore(tmp_path / "sessions")
    repository = UnifiedSessionRepository(events)
    await repository.start()
    await repository.append_event(message(sid, "new", 2))
    UnifiedContextLedger(events).append(sid, "system/message", {"content": "reference"})
    await repository.close()
    # Rerunning after new activity must not fail or duplicate that activity.
    await migrate_sessions(tmp_path)
    rollback_sessions(tmp_path)
    projection = JsonlSessionStore(tmp_path / "conversations-unified-rollback")
    assert [e.content for e in await projection.read_events(sid)] == ["old", "new"]
    await projection.append_event(message(sid, "after-rollback", 4))
    await migrate_sessions(tmp_path)
    events = SessionEventStore(tmp_path / "sessions")
    repository = UnifiedSessionRepository(events)
    assert [e.content for e in await repository.read_events(sid)] == ["old", "new", "after-rollback"]
    assert len(UnifiedContextLedger(events).read(sid)) == 1
    await repository.close()


@run_async
async def test_migration_refuses_running_engine(tmp_path: Path):
    with (tmp_path / "engine.sock.lock").open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(RuntimeError, match="Engine"):
            await migrate_sessions(tmp_path)


@run_async
async def test_migration_rejects_complete_corrupt_tail_without_switching(tmp_path: Path):
    old = JsonlSessionStore(tmp_path / "conversations")
    await old.start()
    session = await old.create_session()
    path = old.sessions_path / session.session_id / "events.jsonl"
    path.write_text('{"schema_version":99}\n')
    with pytest.raises(ValueError):
        await migrate_sessions(tmp_path)
    assert not (tmp_path / "sessions/.migrated-v1").exists()
    assert path.read_text() == '{"schema_version":99}\n'
