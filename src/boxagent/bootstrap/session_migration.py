"""Copy legacy sessions into unified streams; never remove source data."""

from __future__ import annotations

import asyncio
from boxagent.core import file_lock as fcntl
import json
import hashlib
import math
import shutil
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from boxagent.infrastructure.persistence.jsonl_context_ledger import JsonlContextLedgerStore
from boxagent.infrastructure.persistence.jsonl_session_repository import JsonlSessionStore
from boxagent.infrastructure.persistence.session_events import SessionEventStore, write_json
from boxagent.infrastructure.persistence.unified_session import ledger_kind, product_kind
from boxagent.domain.conversation.models import ContextCheckpoint, ProductEvent, RuntimeContextSegment


def validate_legacy_log(path: Path) -> None:
    """Reject complete corrupt records; only an unfinished JSON tail may be omitted."""
    if not path.exists():
        return
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    for position, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            if position == len(lines) - 1 and not raw.endswith("\n"):
                return
            raise ValueError(f"迁移源日志 {path.name} 第 {position + 1} 行损坏") from exc
        if not isinstance(value, dict):
            raise ValueError(f"迁移源日志 {path.name} 必须是 object")
        if path.name == "events.jsonl" and (value.get("schema_version") != 1
                or not isinstance(value.get("event_id"), str)
                or not isinstance(value.get("type"), str)):
            raise ValueError("迁移源正式事件无效")
        if path.name == "events.jsonl":
            event = ProductEvent.from_payload(value)
            if type(event.sequence) is not int or not isinstance(event.occurred_at, (int, float)) \
                    or not math.isfinite(event.occurred_at) or not isinstance(event.session_id, str):
                raise ValueError("迁移源正式事件字段无效")
        if path.name == "context.jsonl" and (value.get("record_type") not in {"checkpoint", "segment"}
                or not isinstance(value.get("payload"), dict)
                or value["payload"].get("schema_version") != 1):
            raise ValueError("迁移源 checkpoint/segment 无效")
        if path.name == "context.jsonl":
            if value["record_type"] == "checkpoint":
                ContextCheckpoint.from_payload(value["payload"])
            else:
                RuntimeContextSegment.from_payload(value["payload"])


def task_manifest(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


@contextmanager
def maintenance_lock(data_dir: Path) -> Iterator[None]:
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "engine.sock.lock").open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("请先停止 Engine，再迁移或回滚") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


async def migrate_sessions(data_dir: Path) -> dict[str, Any]:
    with maintenance_lock(data_dir):
        return await _migrate_sessions(data_dir)


async def _migrate_sessions(data_dir: Path) -> dict[str, Any]:
    rolled_back = (data_dir / "sessions/.rollback-v1").exists()
    source_root = data_dir / ("conversations-unified-rollback" if rolled_back else "conversations")
    legacy = JsonlSessionStore(source_root)
    if not legacy.index_path.exists():
        return {"sessions": 0, "events": 0, "tasks": 0}
    target = SessionEventStore(data_dir / "sessions")
    ledger = JsonlContextLedgerStore(source_root)
    totals = {"sessions": 0, "events": 0, "tasks": 0}
    try:
        index = json.loads(legacy.index_path.read_text(encoding="utf-8"))
        for sid in index["sessions"]:
            directory = legacy.sessions_path / sid
            meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            target.create(sid, created_at=meta["created_at"])
            for filename in ("events.jsonl", "context.jsonl", "context.v1.jsonl"):
                validate_legacy_log(directory / filename)
            events = legacy._read_events(directory / "events.jsonl", repair=False)
            contexts = ledger.read(sid)
            if rolled_back:
                existing_contexts = [e for e in target.read(sid) if "ledger_type" in e.data]
                contexts = contexts[len(existing_contexts):]
            rows: list[tuple[float, int, str, dict[str, Any], str]] = []
            for event in events:
                rows.append((event.occurred_at, event.sequence, product_kind(event),
                             {"product_event": event.payload()}, "product:" + event.event_id))
            for event in contexts:
                rows.append((event.time, event.seq, ledger_kind(event.type),
                             {"ledger_type": event.type, "legacy_ledger_seq": event.seq,
                              **event.data}, f"{'rollback' if rolled_back else 'legacy'}-ledger:{event.seq}"))
            checkpoints, segments = legacy._read_context(directory / "context.jsonl", repair=False)
            for checkpoint in checkpoints:
                segment = next((s for s in segments if s.checkpoint_id == checkpoint.checkpoint_id), None)
                if segment is None:
                    raise ValueError(f"迁移 checkpoint 缺少 segment：{checkpoint.checkpoint_id}")
                rows.append((checkpoint.created_at, 0, "compaction/checkpoint", {
                    "payload": checkpoint.payload(), "segment": segment.payload()},
                    "checkpoint:" + checkpoint.checkpoint_id))
            for at, _, kind, value, key in sorted(rows, key=lambda r: (r[0], r[1])):
                target.append(sid, kind, value, at=at, dedup_key=key)
            target.append(sid, "migration/source", {
                "source": str(source_root.relative_to(data_dir)),
                "formal_events": len(events), "ledger_events": len(contexts),
                "context_history_complete": (directory / "context.v1.jsonl").exists(),
                "note": "无旧账本的会话无法从正式消息还原 provider 输入；原始诊断 trace 保留供只读回放。"
                        if not (directory / "context.v1.jsonl").exists() else "旧账本已迁移"},
                at=meta["created_at"], dedup_key="migration-source:" + source_root.name)
            state_path = target.directory(sid) / "state.json"
            if not state_path.exists() or rolled_back:
                bindings = json.loads((directory / "runtime-bindings.json").read_text(encoding="utf-8"))
                target.save_state(sid, {"metadata": meta, "bindings": bindings["bindings"]})
            migrated = target.read(sid)
            original_keys = {"product:" + e.event_id for e in events}
            actual_product = sorted([e.data["product_event"] for e in migrated
                                     if e.data.get("dedup_key") in original_keys],
                                    key=lambda e: e["sequence"])
            if actual_product != [e.payload() for e in events]:
                raise ValueError(f"迁移正式事件校验失败：{sid}")
            if not rolled_back and len([e for e in migrated if "legacy_ledger_seq" in e.data]) != len(contexts):
                raise ValueError(f"迁移上下文事件校验失败：{sid}")
            for task_id in {e.task_id for e in events if e.task_id}:
                if not isinstance(task_id, str) or "/" in task_id or ".." in task_id:
                    raise ValueError("迁移任务 ID 无效")
                candidates = sorted((data_dir / "runs").glob(f"????-??-??/??????-{task_id}"))
                candidates += [data_dir / "tasks" / task_id, data_dir / "logs/tasks" / task_id]
                source = next((p for p in candidates if p.is_dir()), None)
                destination = target.directory(sid) / "tasks" / task_id
                if source and not destination.exists():
                    temporary = destination.with_name(task_id + ".migrating")
                    if temporary.exists():
                        shutil.rmtree(temporary)
                    await asyncio.to_thread(shutil.copytree, source, temporary)
                    if await asyncio.to_thread(task_manifest, source) != await asyncio.to_thread(task_manifest, temporary):
                        raise ValueError(f"迁移任务附件校验失败：{task_id}")
                    temporary.rename(destination)
                    totals["tasks"] += 1
                elif source and await asyncio.to_thread(task_manifest, source) != await asyncio.to_thread(task_manifest, destination):
                    raise ValueError(f"已迁移任务与源文件不一致：{task_id}")
            totals["sessions"] += 1
            totals["events"] += len(rows)
        target_index = data_dir / "sessions/index.json"
        if not target_index.exists() or rolled_back:
            write_json(target_index, index)
        write_json(data_dir / "sessions/.migrated-v1", {
            "version": 1, "source": "conversations", "totals": totals})
        (data_dir / "sessions/.rollback-v1").unlink(missing_ok=True)
        return totals
    finally:
        target.close()


def rollback_sessions(data_dir: Path) -> None:
    """Export all current facts to a separate legacy projection before switching."""
    with maintenance_lock(data_dir):
        root = data_dir / "sessions"
        target = SessionEventStore(root)
        projection = data_dir / "conversations-unified-rollback"
        marker = root / ".migrated-v1"
        if not marker.exists():
            return
        index = json.loads((root / "index.json").read_text(encoding="utf-8"))
        try:
            for sid in index["sessions"]:
                target.acquire(sid)
                state = target.state(sid)
                rows = target.read(sid)
                directory = projection / "sessions" / sid
                write_json(directory / "metadata.json", state["metadata"])
                write_json(directory / "runtime-bindings.json", {
                    "schema_version": 1, "bindings": state.get("bindings", {})})
                products = sorted((e.data["product_event"] for e in rows
                                   if "product_event" in e.data), key=lambda e: e["sequence"])
                JsonlSessionStore._write_lines(directory / "events.jsonl", [
                    json.dumps(e, ensure_ascii=False) for e in products])
                contexts = [json.dumps({"record_type": kind, "payload": e.data[key]},
                                       ensure_ascii=False)
                            for e in rows if e.type == "compaction/checkpoint"
                            for kind, key in (("segment", "segment"), ("checkpoint", "payload"))]
                JsonlSessionStore._write_lines(directory / "context.jsonl", contexts)
                ledger_rows = [e for e in rows if "ledger_type" in e.data]
                JsonlSessionStore._write_lines(directory / "context.v1.jsonl", [
                    json.dumps({"type": "ledger", "version": 1, "session_id": sid}),
                    *(json.dumps({"seq": i, "type": e.data["ledger_type"], "time": e.time,
                                  "data": {k: v for k, v in e.data.items()
                                           if k not in {"ledger_type", "dedup_key", "legacy_ledger_seq"}}},
                                 ensure_ascii=False)
                      for i, e in enumerate(ledger_rows, 1))])
            write_json(projection / "index.json", index)
            write_json(root / ".rollback-v1", {"version": 1, "projection": projection.name})
            marker.unlink()
        finally:
            target.close()
