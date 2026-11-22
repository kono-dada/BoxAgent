from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path

import pytest

from boxagent.infrastructure.wechat.database.reader import DatabaseReader, table_for

from .conftest import DB, GROUP, PEER, PlainSnapshots, add_message


def test_contacts_sessions_and_synthetic_filehelper(reader: DatabaseReader) -> None:
    assert reader.contacts("立信")[0].peer == PEER
    assert reader.contacts("filehelper")[0].nickname == "文件传输助手"
    assert len(reader.sessions()) == 2
    assert reader.sessions(unread_only=True)[0].unread == 2


def test_group_mute_tracks_wechat_preference_and_keeps_history(reader: DatabaseReader,
                                                            snapshots: PlainSnapshots) -> None:
    assert not reader.group_muted(GROUP)  # Older snapshot without the field.
    add_message(snapshots, 1, "见面改到七点", peer=GROUP)
    connection = sqlite3.connect(snapshots.keys.root / "contact/contact.db")
    try:
        with connection:
            connection.execute("ALTER TABLE contact ADD COLUMN chat_room_notify INTEGER")
            connection.execute(
                "UPDATE contact SET chat_room_notify=0 WHERE username IN (?, ?)", (GROUP, PEER)
            )
        assert reader.group_muted(GROUP)
        assert not reader.group_muted(PEER)
        assert not reader.group_muted("unknown@chatroom")
        assert reader.history(GROUP)[0].text == "见面改到七点"
        with connection:
            connection.execute("UPDATE contact SET chat_room_notify=1 WHERE username=?", (GROUP,))
        assert not reader.group_muted(GROUP)
        with connection:
            connection.execute(
                "UPDATE contact SET chat_room_notify=NULL WHERE username=?", (GROUP,)
            )
        assert not reader.group_muted(GROUP)
    finally:
        connection.close()


def test_history_and_decoded_search(reader: DatabaseReader, snapshots: PlainSnapshots) -> None:
    add_message(snapshots, 1, "会议明天三点")
    add_message(snapshots, 2, "Hello 🎉", sender=1)
    assert [m.local_id for m in reader.history(PEER)] == [1, 2]
    assert reader.search("会议", peer=PEER)[0].local_id == 1
    assert reader.search("hello")[0].text == "Hello 🎉"


def test_receipt_excludes_old_identical_and_incoming_records(
    reader: DatabaseReader, snapshots: PlainSnapshots
) -> None:
    add_message(snapshots, 1, "same", sender=1, server=9)
    cursor = reader.delivery_cursor(PEER)
    add_message(snapshots, 2, "same", sender=2, server=10)
    assert not reader.find_delivery(PEER, "same", after=cursor, timeout=0).matched
    add_message(snapshots, 3, "same", sender=1, server=0)
    pending = reader.find_delivery(PEER, "same", after=cursor, timeout=0)
    assert pending.matched and pending.local_id == 3 and pending.server_id == 0


def test_delayed_server_ack_is_polled(reader: DatabaseReader, snapshots: PlainSnapshots,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = reader.delivery_cursor(PEER)
    add_message(snapshots, 1, "pending", sender=1)

    def acknowledge(_: float) -> None:
        c = sqlite3.connect(snapshots.keys.root / DB)
        with c:
            c.execute(f'UPDATE "{table_for(PEER)}" SET server_id=987 WHERE local_id=1')
        c.close()

    monkeypatch.setattr(time, "sleep", acknowledge)
    assert reader.find_delivery(PEER, "pending", after=cursor, timeout=1).server_id == 987


def test_duplicate_matching_new_outgoing_is_ambiguous(
    reader: DatabaseReader, snapshots: PlainSnapshots
) -> None:
    cursor = reader.delivery_cursor(PEER)
    add_message(snapshots, 1, "same", sender=1)
    add_message(snapshots, 2, "same", sender=1)
    with pytest.raises(ValueError, match="ambiguous"):
        reader.find_delivery(PEER, "same", after=cursor, timeout=0)


def test_members_are_identity_verified_and_use_nickname(reader: DatabaseReader) -> None:
    member = reader.members(GROUP)[0]
    assert member.peer == PEER and member.membership_verified
    assert member.display_name == "Morty"  # Not the local remark 立信.


def test_production_wechat_has_no_experiment_imports() -> None:
    root = Path(__file__).resolve().parents[2] / "src/boxagent"
    paths = [*root.glob("domain/wechat/*.py"), *root.glob("domain/events/*.py"),
             *root.glob("application/wechat/*.py"), *root.glob("infrastructure/wechat/**/*.py"),
             root / "bootstrap/wechat.py", root / "entrypoints/wechat.py"]
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("experiments"), path
                if "/domain/" in str(path):
                    assert not (node.module or "").startswith(
                        ("boxagent.infrastructure", "boxagent.interfaces")
                    ), path
            elif isinstance(node, ast.Import):
                assert all(not alias.name.startswith("experiments") for alias in node.names), path
