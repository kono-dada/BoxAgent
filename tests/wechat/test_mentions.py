from __future__ import annotations

import pytest

from boxagent.domain.wechat.models import GroupMember
from boxagent.infrastructure.wechat.database.reader import DatabaseReader
from boxagent.infrastructure.wechat.mentions import validate_member

from .conftest import GROUP, PEER, PlainSnapshots, add_message


def test_mention_requires_verified_membership_in_exact_group() -> None:
    with pytest.raises(ValueError, match="verified member"):
        validate_member(GROUP, GroupMember(GROUP, PEER, "老师", False))
    with pytest.raises(ValueError, match="verified member"):
        validate_member(GROUP, GroupMember("other@chatroom", PEER, "老师", True))


def test_long_native24_labels_are_refused_without_truncation() -> None:
    with pytest.raises(ValueError, match="short-string"):
        validate_member(GROUP, GroupMember(GROUP, PEER, "长"*20, True))


def test_receipt_requires_real_atuserlist(
    reader: DatabaseReader, snapshots: PlainSnapshots
) -> None:
    cursor = reader.delivery_cursor(GROUP)
    add_message(snapshots, 1, "@Morty\u2005你好", sender=1, peer=GROUP)
    assert not reader.find_delivery(GROUP, "你好", after=cursor, timeout=0, mention=PEER).matched
    # A literal @ display name does not prove a real mention.
    add_message(snapshots, 2, "@Morty\u2005你好", sender=1, server=999, peer=GROUP,
                source=f"<msgsource><atuserlist>{PEER}</atuserlist></msgsource>")
    result = reader.find_delivery(GROUP, "你好", after=cursor, timeout=0, mention=PEER)
    assert result.local_id == 2 and result.server_id == 999
