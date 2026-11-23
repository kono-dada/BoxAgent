"""Durable per-user inbox, leased runs and cached notification decisions."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from boxagent.domain.events.models import EventBatch, EventDecision, EventEnvelope, EventReceipt


def event_from_json(raw: str) -> EventEnvelope:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Inbox event must be an object")
    required = ("event_id", "source", "account_id", "event_type", "subject_id", "content")
    if not all(isinstance(value.get(k), str) for k in required):
        raise ValueError("Invalid inbox event identity/content")
    if not all(isinstance(value.get(k), (int, float)) for k in ("occurred_at", "observed_at")):
        raise ValueError("Invalid inbox event timestamps")
    allowed = EventEnvelope.__dataclass_fields__
    for key in ("sender_id", "sender_name", "subject_name", "payload_ref"):
        if key in value and not isinstance(value[key], str):
            raise ValueError("Invalid event reference")
    if value.get("correlation_id") is not None and not isinstance(value["correlation_id"], str):
        raise ValueError("Invalid event correlation")
    return EventEnvelope(**{k: v for k, v in value.items() if k in allowed})


def decision_from_json(raw: str) -> EventDecision:
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid decision")
    action, summary, reason, identifiers = (
        value.get(k) for k in ("action", "summary", "reason", "source_event_ids")
    )
    if (
        not isinstance(action, str)
        or action not in {"notify", "silent"}
        or not isinstance(summary, str)
        or not isinstance(reason, str)
    ):
        raise ValueError("Invalid notification decision")
    if not isinstance(identifiers, list) or not all(isinstance(i, str) for i in identifiers):
        raise ValueError("Invalid decision references")
    return EventDecision(str(action), summary, tuple(identifiers), reason)


class SQLiteEventInbox:
    def __init__(self, path: Path, *, lease_seconds: float = 120,
                 live_window_seconds: float = 120) -> None:
        if lease_seconds <= 0 or live_window_seconds <= 0:
            raise ValueError("Inbox timing windows must be positive")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        self.path = path
        self.lease_seconds = lease_seconds
        self.live_window_seconds = live_window_seconds
        c = self._connect()
        try:
            c.executescript("\n                CREATE TABLE IF NOT EXISTS inbox(\n             "
                "       event_key TEXT PRIMARY KEY, source TEXT NOT NULL, account "
                "TEXT NOT NULL,\n                    subject TEXT NOT NULL, "
                "payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',\n   "
                "                 run_id TEXT, due REAL NOT NULL DEFAULT 0, "
                "created REAL NOT NULL);\n                CREATE TABLE IF NOT "
                "EXISTS event_runs(\n                    run_id TEXT PRIMARY KEY, "
                "lease TEXT NOT NULL, lease_until REAL NOT NULL,\n                 "
                "   state TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', "
                "decision TEXT,\n                    attempts INTEGER NOT NULL "
                "DEFAULT 0, error TEXT NOT NULL DEFAULT '');\n                "
                "CREATE INDEX IF NOT EXISTS inbox_pending ON inbox(state,due,"
                "created);\n                CREATE INDEX IF NOT EXISTS inbox_batch "
                "ON inbox(state,source,account,subject,created);\n                "
                "CREATE INDEX IF NOT EXISTS inbox_run ON inbox(run_id);\n          "
                "  ")
        finally:
            c.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def put(self, event: EventEnvelope) -> EventReceipt:
        if not all((event.source, event.account_id, event.event_id, event.subject_id)):
            raise ValueError("External event identity must be nonempty")
        key = hashlib.sha256(
            json.dumps([event.source, event.account_id, event.event_id]).encode()
        ).hexdigest()
        c = self._connect()
        try:
            with c:
                c.execute(
                    "INSERT OR IGNORE INTO inbox(event_key,source,account,subject,payload,"
                    "created) VALUES (?,?,?,?,?,?)",
                    (
                        key,
                        event.source,
                        event.account_id,
                        event.subject_id,
                        json.dumps(asdict(event), ensure_ascii=False),
                        time.time(),
                    ),
                )
            return EventReceipt(event.event_id, True)
        finally:
            c.close()

    def claim(self, *, limit: int = 10) -> EventBatch | None:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid event batch limit")
        now, lease = time.time(), uuid.uuid4().hex
        c = self._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            # Recover the same run and cached decision after a crash/retry.
            previous = c.execute(
                "SELECT run_id,session_id,decision FROM event_runs "
                "WHERE state='processing' AND lease_until<=? ORDER BY rowid LIMIT 1",
                (now,),
            ).fetchone()
            if previous is not None:
                run_id, session_id, raw_decision = previous
                c.execute(
                    "UPDATE event_runs SET lease=?,lease_until=?,attempts=attempts+1 WHERE "
                    "run_id=?",
                    (lease, now + self.lease_seconds, run_id),
                )
            else:
                # Fresh messages must not wait behind an offline catch-up backlog.
                # Process durable old messages in their original order when the live queue is empty.
                live_after = now - self.live_window_seconds
                first = c.execute(
                    "SELECT source,account,subject,"
                    "(CAST(json_extract(payload,'$.occurred_at') AS REAL)>=?) AS recent "
                    "FROM inbox WHERE state='pending' AND due<=? "
                    "ORDER BY recent DESC,created,rowid LIMIT 1",
                    (live_after, now),
                ).fetchone()
                if first is None:
                    return None
                run_id, session_id, raw_decision = "evrun_"+uuid.uuid4().hex, "", None
                source, account, subject, recent = first
                keys = [
                    row[0]
                    for row in c.execute(
                        "SELECT event_key FROM inbox WHERE state='pending' AND due<=? "
                        "AND source=? AND account=? AND subject=? "
                        "AND (?=0 OR CAST(json_extract(payload,'$.occurred_at') AS REAL)>=?) "
                        "ORDER BY created,rowid LIMIT ?",
                        (now, source, account, subject, recent, live_after, limit),
                    )
                ]
                c.execute(
                    "INSERT INTO event_runs(run_id,lease,lease_until,state,attempts) VALUES (?,?,"
                    "?,'processing',1)",
                    (run_id, lease, now + self.lease_seconds),
                )
                c.executemany(
                    "UPDATE inbox SET state='processing',run_id=? WHERE event_key=?",
                    [(run_id, k) for k in keys],
                )
            payloads = [
                row[0]
                for row in c.execute(
                    "SELECT payload FROM inbox WHERE run_id=? ORDER BY created,rowid", (run_id,)
                )
            ]
            c.commit()
            return EventBatch(
                str(run_id),
                lease,
                tuple(event_from_json(str(p)) for p in payloads),
                str(session_id),
                decision_from_json(str(raw_decision)) if raw_decision else None,
            )
        finally:
            c.close()

    def _update(self, batch: EventBatch, sql: str, parameters: tuple[object, ...]) -> None:
        c = self._connect()
        try:
            with c:
                result = c.execute(
                    sql + " WHERE run_id=? AND lease=? AND state='processing'",
                    (*parameters, batch.run_id, batch.lease),
                )
                if result.rowcount != 1:
                    raise ValueError("Event run lease was lost")
        finally:
            c.close()

    def renew(self, batch: EventBatch) -> None:
        self._update(
            batch, "UPDATE event_runs SET lease_until=?", (time.time() + self.lease_seconds,)
        )

    def bind_session(self, batch: EventBatch, session_id: str) -> None:
        self._update(batch, "UPDATE event_runs SET session_id=?", (session_id,))

    def save_decision(self, batch: EventBatch, decision: EventDecision) -> None:
        self._update(
            batch,
            "UPDATE event_runs SET decision=?",
            (json.dumps(asdict(decision), ensure_ascii=False),),
        )

    def complete(self, batch: EventBatch) -> None:
        c = self._connect()
        try:
            with c:
                result = c.execute(
                    "UPDATE event_runs SET state='done' WHERE run_id=? AND lease=? AND "
                    "state='processing'",
                    (batch.run_id, batch.lease),
                )
                if result.rowcount != 1:
                    raise ValueError("Event run lease was lost")
                c.execute("UPDATE inbox SET state='done' WHERE run_id=?", (batch.run_id,))
        finally:
            c.close()

    def retry(self, batch: EventBatch, error: str) -> None:
        self._update(
            batch, "UPDATE event_runs SET lease_until=?,error=?", (time.time() + 5, error[:200])
        )

    def status(self) -> dict[str, int]:
        c = self._connect()
        try:
            result = {
                str(state): int(count)
                for state, count in c.execute("SELECT state,count(*) FROM inbox GROUP BY state")
            }
            for name in ("pending", "processing", "done"):
                result.setdefault(name, 0)
            result["runs"] = int(c.execute("SELECT count(*) FROM event_runs").fetchone()[0])
            return result
        finally:
            c.close()
