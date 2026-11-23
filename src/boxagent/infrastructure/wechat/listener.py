"""Private cursor/outbox storage for the standalone listener experiment."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TypedDict, cast

from boxagent.domain.events.models import EventEnvelope, EventReceipt
from boxagent.domain.wechat.models import CALL_PREVIEW, PendingMessage
from boxagent.infrastructure.wechat._private import private_dir
from boxagent.infrastructure.wechat.database.reader import TABLE, text
from boxagent.infrastructure.wechat.database.snapshot import (
    FileStamp,
    SnapshotRepository,
    signature,
)


class MessageEvent(TypedDict):
    event_id: str
    database: str
    table: str
    local_id: int
    server_id: str  # Preserve 64-bit identifiers in JSON consumers.
    chat_id: str | None
    sender_id: str
    text: str
    local_type: int
    created_at: int
    detected_at: float
    observed_at: float


@dataclass(frozen=True)
class MessageRow:
    table: str
    local_id: int
    server_id: str
    chat_id: str | None
    sender_id: str
    text: str
    local_type: int
    created_at: int


class _ListenerState:
    """Commit new events and their watermarks in the same SQLite transaction.

    Callback delivery is at least once. Consumers must deduplicate event_id;
    success in a child process and a parent crash cannot be one transaction.
    """

    def __init__(self, path: Path, binding: str) -> None:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS binding(value TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY, generation TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors(
                source TEXT, table_name TEXT, local_id INTEGER NOT NULL,
                PRIMARY KEY(source, table_name));
            CREATE TABLE IF NOT EXISTS events(
                event_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0);
        """)
        row = self.connection.execute("SELECT value FROM binding").fetchone()
        if row is not None and row[0] != binding:
            self.close()
            raise ValueError(
                "State belongs to another account/path/filter; use a different --state-dir"
            )
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO binding VALUES (?)", (binding,))

    def close(self) -> None:
        self.connection.close()

    def cursors(self, source: str, generation: str) -> dict[str, int] | None:
        row = self.connection.execute(
            "SELECT generation FROM sources WHERE name=?", (source,)
        ).fetchone()
        if row is None:
            return None
        if row[0] != generation:
            raise ValueError("Source database identity changed; refusing to reuse old cursors")
        return {str(table): int(identifier) for table, identifier in self.connection.execute(
            "SELECT table_name,local_id FROM cursors WHERE source=?", (source,))}

    def baseline(self, source: str, generation: str, maxima: dict[str, int]) -> None:
        with self.connection:
            self.connection.execute("INSERT INTO sources VALUES (?,?)", (source, generation))
            self.connection.executemany(
                "INSERT INTO cursors VALUES (?,?,?)",
                [(source, table, identifier) for table, identifier in maxima.items()],
            )

    def stage(self, source: str, generation: str, maxima: dict[str, int], rows: list[MessageRow],
              *, detected_at: float, observed_at: float) -> int:
        cursors = self.cursors(source, generation)
        if cursors is None:
            raise ValueError("Source baseline is missing")
        if any(
            table not in maxima or maxima[table] < previous for table, previous in cursors.items()
        ):
            raise ValueError("Source table disappeared or local_id regressed; cursors retained")
        inserted = 0
        with self.connection:
            for row in rows:
                if row.local_id <= cursors.get(row.table, 0):
                    continue
                event_id = hashlib.sha256(json.dumps(
                    [source, generation, row.table, row.local_id], separators=(",", ":"),
                ).encode()).hexdigest()
                event: MessageEvent = {
                    "event_id": event_id, "database": source, "table": row.table,
                    "local_id": row.local_id, "server_id": row.server_id,
                    "chat_id": row.chat_id, "sender_id": row.sender_id, "text": row.text,
                    "local_type": row.local_type, "created_at": row.created_at,
                    "detected_at": detected_at, "observed_at": observed_at,
                }
                result = self.connection.execute(
                    "INSERT OR IGNORE INTO events(event_id,payload) VALUES (?,?)",
                    (event_id, json.dumps(event, ensure_ascii=False)),
                )
                inserted += result.rowcount
            self.connection.executemany(
                "INSERT INTO cursors VALUES (?,?,?) ON CONFLICT(source,table_name) "
                "DO UPDATE SET local_id=excluded.local_id",
                [(source, table, identifier) for table, identifier in maxima.items()],
            )
        return inserted

    def pending(self, now: float, *, limit: int = 100) -> list[tuple[MessageEvent, int]]:
        result: list[tuple[MessageEvent, int]] = []
        for payload, attempts in self.connection.execute(
            "SELECT payload,attempts FROM events WHERE delivered=0 AND retry_at<=? "
            "ORDER BY rowid LIMIT ?", (now, limit),
        ):
            value: object = json.loads(str(payload))
            strings = ("event_id", "database", "table", "server_id", "sender_id", "text")
            integers = ("local_id", "local_type", "created_at")
            if not isinstance(value, dict):
                raise ValueError("Stored source event is not an object")
            if (
                not all(isinstance(value.get(k), str) for k in strings)
                or not all(type(value.get(k)) is int for k in integers)
                or not all(
                    isinstance(value.get(k), (int, float)) for k in ("detected_at", "observed_at")
                )
                or not (value.get("chat_id") is None or isinstance(value.get("chat_id"), str))
            ):
                raise ValueError("Stored source event does not match the validated schema")
            result.append((cast(MessageEvent, value), int(attempts)))
        return result

    def finish(self, event_id: str, *, delivered: bool, now: float) -> None:
        with self.connection:
            self.connection.execute("UPDATE events SET delivered=?,attempts=attempts+1,retry_at=? "
                                    "WHERE event_id=?", (int(delivered), now+2, event_id))

    def counts(self) -> dict[str, int]:
        total, delivered, exhausted = self.connection.execute(
            "SELECT count(*),coalesce(sum(delivered),0),"
            "coalesce(sum(delivered=0 AND attempts>=3),0) FROM events",
        ).fetchone()
        return {"total": int(total), "delivered": int(delivered),
                "pending": int(total)-int(delivered), "exhausted": int(exhausted)}



_CALL_TYPE = 50

def integer(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError("Unexpected integer in message schema")


def _scan_snapshot(path: Path, cursors: dict[str, int] | None, *, self_wxid: str,
                  peer: str | None) -> tuple[dict[str, int], list[MessageRow], int]:
    """Return maxima INCLUDING excluded/self rows, so skipped rows are consumed."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {integer(identifier): str(name) for identifier, name in connection.execute(
            "SELECT rowid,user_name FROM Name2Id")}
        if self_wxid not in names.values():
            raise ValueError("Current account missing from Name2Id; check --self-wxid")
        chats = {"Msg_" + hashlib.md5(name.encode()).hexdigest(): name for name in names.values()}
        tables = [
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if isinstance(row[0], str) and TABLE.fullmatch(row[0])
        ]
        maxima: dict[str, int] = {}
        messages: list[MessageRow] = []
        skipped = 0
        wanted = "Msg_"+hashlib.md5(peer.encode()).hexdigest() if peer else None
        for table in tables:
            maximum = integer(
                connection.execute(f'SELECT coalesce(max(local_id),0) FROM "{table}"').fetchone()[0]
            )
            maxima[table] = maximum
            if cursors is None or maximum <= cursors.get(table, 0):
                continue
            # Advance other tables too; changing --wxid requires another state.
            if wanted is not None and table != wanted:
                continue
            columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
            required = {
                "local_id",
                "server_id",
                "real_sender_id",
                "local_type",
                "create_time",
                "message_content",
            }
            if not required <= columns:
                raise ValueError("Unsupported message table schema")
            compression = "WCDB_CT_message_content" if "WCDB_CT_message_content" in columns else "0"
            rows = connection.execute(
                "SELECT local_id,server_id,real_sender_id,local_type,create_time,message_content,"
                f"{compression}"
                " FROM \""
                f"{table}"
                "\" WHERE local_id>? ORDER BY local_id LIMIT 5001",
                (cursors.get(table, 0),),
            ).fetchall()
            if len(rows) > 5000:
                raise ValueError("Probe backlog exceeds 5000 rows; cursors retained for inspection")
            for identifier, server, sender, kind, created, body, compressed in rows:
                if integer(kind) & 0xffffffff == 10000:
                    skipped += 1
                    continue  # System records are not incoming person messages.
                sender_name = names.get(integer(sender))
                if sender_name is None:
                    raise ValueError(
                        "Unresolved message sender; refusing to guess incoming direction"
                    )
                if sender_name == self_wxid:
                    skipped += 1
                    continue
                if isinstance(body, bytes):
                    content = (
                        text(body, 4) if compressed == 4 else body.decode("utf-8", errors="replace")
                    )
                elif isinstance(body, str):
                    content = body
                else:
                    content = ""
                messages.append(MessageRow(table, integer(identifier), str(server or 0),
                                           peer or chats.get(table), sender_name, content,
                                           integer(kind), integer(created)))
        return maxima, messages, skipped
    finally:
        connection.close()


class DatabaseMessageSource:
    """A source outbox hands off to EventSink with stable, account-bound IDs."""

    def __init__(
        self, snapshots: SnapshotRepository, state_dir: Path, *, peer: str | None = None
    ) -> None:
        import fcntl
        self.snapshots = snapshots
        self.peer = peer
        self._lock = threading.RLock()
        self._closed = False
        directory = private_dir(state_dir)
        self._lease = os.open(
            directory / "source.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            binding = hashlib.sha256(
                json.dumps([str(snapshots.keys.root), snapshots.keys.account_id, peer]).encode()
            ).hexdigest()
            self._state = _ListenerState(directory / "source.sqlite", binding)
        except BaseException:
            os.close(self._lease)
            raise
        self._sources = snapshots.keys.messages()
        self._seen: dict[str, tuple[FileStamp | None, FileStamp | None]] = {}

    def poll(self) -> int:
        with self._lock, self.snapshots.lock:
            if self._closed:
                raise RuntimeError("Message source is closed")
            if self.snapshots.keys.messages() != self._sources:
                raise ValueError("Shard set changed; recreate the account-bound source")
            staged = 0
            for name in self._sources:
                database = self.snapshots.source(name)
                current = signature(database.path)
                if current == self._seen.get(name):
                    continue
                detected = time.time()
                output, _cost, stable = database.refresh("listen")
                cursors = self._state.cursors(name, database.generation)
                maxima, rows, _skipped = _scan_snapshot(output, cursors,
                    self_wxid=self.snapshots.keys.self_wxid, peer=self.peer)
                if cursors is None:
                    self._state.baseline(name, database.generation, maxima)
                else:
                    staged += self._state.stage(name, database.generation, maxima, rows,
                                               detected_at=detected, observed_at=time.time())
                self._seen[name] = stable
                database.last_signature = stable
            return staged

    def pending(self, *, limit: int = 100) -> tuple[PendingMessage, ...]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Message source is closed")
            result: list[PendingMessage] = []
            for item, attempts in self._state.pending(time.time(), limit=limit):
                # Call records contain protocol metadata rather than user text.
                content = (
                    CALL_PREVIEW if item["local_type"] & 0xFFFFFFFF == _CALL_TYPE else item["text"]
                )
                event = EventEnvelope(
                    event_id=item["event_id"],
                    source="wechat",
                    account_id=self.snapshots.keys.account_id,
                    event_type="message.received",
                    subject_id=item["chat_id"] or item["table"],
                    occurred_at=item["created_at"],
                    observed_at=item["observed_at"],
                    content=content,
                    sender_id=item["sender_id"],
                    payload_ref=f'{item["database"]}/{item["table"]}/{item["local_id"]}',
                )
                result.append(PendingMessage(event, attempts))
            return tuple(result)

    def acknowledge(self, event_id: str, *, accepted: bool) -> None:
        with self._lock:
            self._state.finish(event_id, delivered=accepted, now=time.time())

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._state.close()
                os.close(self._lease)
                self._closed = True


class JsonlEventSink:
    """Local CLI destination, not the future Agent inbox. Fsync before receipt."""
    def __init__(self, path: Path) -> None:
        self.path = path
        private_dir(path.parent)

    async def accept(self, event: EventEnvelope) -> EventReceipt:
        return await asyncio.to_thread(self._accept, event)

    def _accept(self, event: EventEnvelope) -> EventReceipt:
        import fcntl
        fd = os.open(self.path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            known: set[str] = set()
            for line in stream:
                value: object = json.loads(line)
                if not isinstance(value, dict) or not isinstance(value.get("event_id"), str):
                    raise ValueError("Corrupt event sink; refusing to acknowledge")
                known.add(value["event_id"])
            if event.event_id not in known:
                stream.write(json.dumps(asdict(event), ensure_ascii=False)+"\n")
                stream.flush()
                os.fsync(stream.fileno())
        return EventReceipt(event.event_id, True)
