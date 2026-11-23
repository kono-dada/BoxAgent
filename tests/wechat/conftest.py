from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from boxagent.infrastructure.wechat.database.reader import DatabaseReader, table_for
from boxagent.infrastructure.wechat.database.snapshot import (
    FileStamp,
    RefreshCost,
    SnapshotRepository,
    signature,
)

SELF = "wxid_self"
PEER = "wxid_friend"
GROUP = "room@chatroom"
DB = "message/message_0.db"


@dataclass
class PlainKeys:
    root: Path
    account_id: str = "wechat:fixture"
    self_wxid: str = SELF

    def messages(self) -> tuple[str, ...]:
        return (DB,)


@dataclass
class PlainDatabase:
    path: Path
    generation: str = "fixture-generation"
    last_signature: tuple[FileStamp | None, FileStamp | None] | None = None

    def refresh(
        self, reason: str
    ) -> tuple[Path, RefreshCost, tuple[FileStamp | None, FileStamp | None]]:
        cost = RefreshCost(DB, reason, 0, 0, 0, 0, 0, 0, 0, 1)
        return self.path, cost, signature(self.path)


@dataclass
class PlainSnapshots:
    keys: PlainKeys
    lock: threading.RLock = field(default_factory=threading.RLock)
    _sources: dict[str, PlainDatabase] = field(default_factory=dict)

    def refresh(self, relative: str, *, force: bool = False) -> Path:
        return self.keys.root / relative

    def source(self, relative: str) -> PlainDatabase:
        if relative not in self._sources:
            self._sources[relative] = PlainDatabase(self.keys.root / relative)
        return self._sources[relative]

    def repository(self) -> SnapshotRepository:
        return cast(SnapshotRepository, self)


def add_message(snapshots: PlainSnapshots, identifier: int, body: str = "你好", *, sender: int = 2,
                server: int = 0, peer: str = PEER, source: str = "", local_type: int = 1) -> None:
    connection = sqlite3.connect(snapshots.keys.root / DB)
    with connection:
        connection.execute(f'INSERT INTO "{table_for(peer)}" VALUES (?,?,?,?,?,?,0,?,0)',
                           (identifier, server, sender, local_type, int(time.time()), body, source))
    connection.close()


@pytest.fixture
def snapshots(tmp_path: Path) -> PlainSnapshots:
    for folder in ("contact", "session", "message"):
        (tmp_path / folder).mkdir()
    connection = sqlite3.connect(tmp_path / "contact/contact.db")
    connection.executescript("""
        CREATE TABLE contact(id INTEGER PRIMARY KEY,username TEXT,nick_name TEXT,remark TEXT);
        INSERT INTO contact VALUES (1,'wxid_self','自己',''),(2,'wxid_friend','Morty','立信'),
          (3,'room@chatroom','测试群',''),(4,'wxid_a','同名',''),(5,'wxid_b','同名','');
        CREATE TABLE chatroom_member(room_id INTEGER,member_id INTEGER);
        INSERT INTO chatroom_member VALUES (3,2);
    """)
    connection.close()
    connection = sqlite3.connect(tmp_path / "session/session.db")
    connection.executescript(
        "\n        CREATE TABLE SessionTable(username TEXT,unread_count INTEGER,summary TEXT,"
        "sort_timestamp INTEGER);\n        INSERT INTO SessionTable VALUES ('wxid_friend',2,"
        "'新的消息',100),('room@chatroom',0,'群聊',99);\n    "
    )
    connection.close()
    connection = sqlite3.connect(tmp_path / DB)
    connection.executescript("CREATE TABLE Name2Id(user_name TEXT); INSERT INTO Name2Id VALUES "
                             "('wxid_self'),('wxid_friend'),('room@chatroom');")
    for peer in (PEER, GROUP):
        connection.execute(
            f'CREATE TABLE "{table_for(peer)}" (local_id INTEGER PRIMARY KEY,server_id INTEGER,'
            "real_sender_id INTEGER,local_type INTEGER,create_time INTEGER,message_content BLOB,"
            "WCDB_CT_message_content INTEGER,source BLOB,WCDB_CT_source INTEGER)"
        )
    connection.commit()
    connection.close()
    return PlainSnapshots(PlainKeys(tmp_path))


@pytest.fixture
def reader(snapshots: PlainSnapshots) -> DatabaseReader:
    return DatabaseReader(snapshots.repository())
