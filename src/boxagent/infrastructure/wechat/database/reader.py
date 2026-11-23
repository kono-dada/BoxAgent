"""Read current private snapshots, including compressed bodies and async receipts."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from boxagent.domain.wechat.models import (
    Contact,
    Conversation,
    DeliveryCheck,
    DeliveryCursor,
    GroupMember,
    Message,
)
from boxagent.infrastructure.wechat.database.snapshot import SnapshotRepository

TABLE = re.compile(r"Msg_[0-9a-f]{32}\Z")


def text(value: object, compression: object = 0) -> str:
    if isinstance(value, bytes):
        if compression == 4:
            import zstandard
            return zstandard.ZstdDecompressor().decompress(value).decode("utf-8", errors="strict")
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def table_for(peer: str) -> str:
    return "Msg_" + hashlib.md5(peer.encode()).hexdigest()


def bounded(limit: int) -> int:
    if not 1 <= limit <= 10000:
        raise ValueError("limit must be between 1 and 10000")
    return limit


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


class DatabaseReader:
    def __init__(self, snapshots: SnapshotRepository) -> None:
        self.snapshots = snapshots
        self.account_id = snapshots.keys.account_id
        self.self_wxid = snapshots.keys.self_wxid

    def contacts(self, query: str = "", *, limit: int = 100) -> tuple[Contact, ...]:
        bounded(limit)
        with self.snapshots.lock:
            connection = connect(self.snapshots.refresh("contact/contact.db"))
            try:
                rows = connection.execute("SELECT username,nick_name,remark FROM contact "
                                          "WHERE username<>'' ORDER BY username").fetchall()
            finally:
                connection.close()
        contacts = [Contact(str(peer), text(nick), text(remark)) for peer, nick, remark in rows]
        if not any(c.peer == "filehelper" for c in contacts):
            contacts.append(Contact("filehelper", "文件传输助手"))
        lowered = query.casefold()
        return tuple(c for c in contacts if not query or any(lowered in v.casefold()
                     for v in (c.peer, c.nickname, c.remark)))[:limit]

    def group_muted(self, peer: str) -> bool:
        if not peer.endswith("@chatroom"):
            return False
        with self.snapshots.lock:
            connection = connect(self.snapshots.refresh("contact/contact.db"))
            try:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(contact)")}
                if "chat_room_notify" not in columns:
                    return False
                row = connection.execute("SELECT chat_room_notify FROM contact WHERE username=?",
                                         (peer,)).fetchone()
                # The field represents whether group notifications are enabled.
                return row is not None and row[0] == 0
            finally:
                connection.close()

    def sessions(self, *, unread_only: bool = False, limit: int = 20) -> tuple[Conversation, ...]:
        bounded(limit)
        names = {c.peer: c.display_name for c in self.contacts(limit=10000)}
        with self.snapshots.lock:
            connection = connect(self.snapshots.refresh("session/session.db"))
            try:
                where = "WHERE unread_count>0" if unread_only else ""
                rows = connection.execute(
                    "SELECT username,unread_count,summary,sort_timestamp FROM SessionTable "
                    + where
                    + " ORDER BY sort_timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            finally:
                connection.close()
        return tuple(
            Conversation(
                str(peer),
                names.get(str(peer), str(peer)),
                int(unread or 0),
                text(summary),
                int(updated or 0),
            )
            for peer, unread, summary, updated in rows
        )

    def _messages(self, database: str, peer: str | None, *, after: int = 0) -> list[Message]:
        with self.snapshots.lock:
            connection = connect(self.snapshots.refresh(database))
            try:
                names = {int(identifier): str(name) for identifier, name in connection.execute(
                    "SELECT rowid,user_name FROM Name2Id")}
                peers = {table_for(name): name for name in names.values()}
                tables = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                    if isinstance(row[0], str) and TABLE.fullmatch(row[0])
                ]
                result: list[Message] = []
                for table in tables:
                    if peer is not None and table != table_for(peer):
                        continue
                    columns = {
                        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
                    }
                    compression = (
                        "WCDB_CT_message_content" if "WCDB_CT_message_content" in columns else "0"
                    )
                    source = "source" if "source" in columns else "NULL"
                    source_compression = "WCDB_CT_source" if "WCDB_CT_source" in columns else "0"
                    rows = connection.execute(
                        "SELECT local_id,server_id,real_sender_id,local_type,create_time,"
                        "message_content,"
                        f"{compression}"
                        ","
                        f"{source}"
                        ","
                        f"{source_compression}"
                        " FROM \""
                        f"{table}"
                        "\" WHERE local_id>? ORDER BY local_id",
                        (after,),
                    )
                    for (
                        identifier,
                        server,
                        sender,
                        kind,
                        created,
                        body,
                        compressed,
                        raw_source,
                        compressed_source,
                    ) in rows:
                        result.append(
                            Message(
                                peer or peers.get(table, table),
                                database,
                                int(identifier),
                                str(server or 0),
                                names.get(int(sender or 0), ""),
                                text(body, compressed),
                                int(kind or 0),
                                int(created or 0),
                                text(raw_source, compressed_source),
                            )
                        )
                return result
            finally:
                connection.close()

    def history(self, peer: str, *, limit: int = 50) -> tuple[Message, ...]:
        bounded(limit)
        records = [
            row for source in self.snapshots.keys.messages() for row in self._messages(source, peer)
        ]
        records.sort(key=lambda m: (m.created_at, m.database, m.local_id))
        return tuple(records[-limit:])

    def search(
        self, query: str, *, peer: str | None = None, limit: int = 50
    ) -> tuple[Message, ...]:
        bounded(limit)
        if not query.strip():
            raise ValueError("Search text is empty")
        result: list[Message] = []
        for source in self.snapshots.keys.messages():
            result.extend(
                row
                for row in self._messages(source, peer)
                if query.casefold() in row.text.casefold()
            )
        result.sort(key=lambda m: (m.created_at, m.database, m.local_id), reverse=True)
        return tuple(result[:limit])

    def members(self, group: str) -> tuple[GroupMember, ...]:
        if not group.endswith("@chatroom"):
            raise ValueError("Group members require an exact chatroom ID")
        with self.snapshots.lock:
            connection = connect(self.snapshots.refresh("contact/contact.db"))
            try:
                rows = connection.execute(
                    "SELECT member.username,member.nick_name FROM chatroom_member m "
                    "JOIN contact room ON room.id=m.room_id "
                    "JOIN contact member ON member.id=m.member_id WHERE room.username=? "
                    "ORDER BY member.username", (group,),
                ).fetchall()
            finally:
                connection.close()
        # Nickname is used, never the local remark. Native per-room aliases
        # are not decoded yet; callers can provide an explicit display name.
        return tuple(
            GroupMember(group, str(peer), text(nick) or str(peer), True) for peer, nick in rows
        )

    def delivery_cursor(self, peer: str) -> DeliveryCursor:
        values: list[tuple[str, int]] = []
        for source in self.snapshots.keys.messages():
            records = self._messages(source, peer)
            values.append((source, max((r.local_id for r in records), default=0)))
        return DeliveryCursor(tuple(values))

    def find_delivery(self, peer: str, text: str, *, after: DeliveryCursor,
                      timeout: float = 25.0, mention: str | None = None) -> DeliveryCheck:
        if not 0 <= timeout <= 60:
            raise ValueError("Invalid receipt timeout")
        deadline = time.monotonic()+timeout
        latest = DeliveryCheck(None, None, False)
        while True:
            matches: list[Message] = []
            for source, cursor in after.watermarks:
                for row in self._messages(source, peer, after=cursor):
                    if row.sender_id != self.self_wxid:
                        continue
                    if (
                        mention is None
                        and row.text == text
                        or mention is not None
                        and self._mention_matches(row, text, mention)
                    ):
                        matches.append(row)
            if len(matches) > 1:
                raise ValueError("Multiple new outgoing records match; delivery is ambiguous")
            if matches:
                message = matches[0]
                latest = DeliveryCheck(message.local_id, int(message.server_id), True)
                if latest.server_id:
                    return latest
            if time.monotonic() >= deadline:
                return latest
            time.sleep(min(1.0, max(0, deadline-time.monotonic())))

    @staticmethod
    def _mention_matches(message: Message, suffix: str, member: str) -> bool:
        if "\u2005" not in message.text or message.text.split("\u2005", 1)[1] != suffix:
            return False
        try:
            element = ET.fromstring(message.source)
        except ET.ParseError:
            return False
        targets = element.findtext(".//atuserlist") or ""
        return member in {p.strip() for p in targets.split(",")}
