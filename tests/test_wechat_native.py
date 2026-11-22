"""Unit tests for the native WeChat sender (no live client required)."""

from __future__ import annotations

import pytest

from boxagent.infrastructure.wechat.addresses import BuildProfile, UnsupportedBuild, profile_for
from boxagent.infrastructure.wechat.probe import LiveObjects
from boxagent.infrastructure.wechat.sender import (
    DraftNotEmpty,
    NativeWeChatSender,
    SendRejected,
    qstring_blob,
)

BASE = 0x128EFC000
LIVE = LiveObjects(vm=0x7510A80298, editor=0x750EC7FB80, view=0x750C0DA100, cell=0x750C4F1C18, peer_object=0x750C4F1D70)


class FakeProbe:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, str, BuildProfile]] = []

    def locate(self, base: int, peer: str, profile: BuildProfile) -> LiveObjects:
        self.calls.append((base, peer, profile))
        if self.error is not None:
            raise self.error
        return LIVE


class FakeExecutor:
    def __init__(self, queue: list[int | None]) -> None:
        self.queue = list(queue)
        self.evaluations: list[dict[str, str]] = []

    def base_address(self) -> int:
        return BASE

    def evaluate(self, expressions: dict[str, str]) -> dict[str, int | None]:
        self.evaluations.append(expressions)
        return {name: self.queue.pop(0) for name in expressions}


def _sender(executor: FakeExecutor, probe: FakeProbe | None = None) -> NativeWeChatSender:
    return NativeWeChatSender(probe or FakeProbe(), executor, profile_for("4.1.15.270115"))


def test_profile_matches_verified_offsets() -> None:
    profile = profile_for("4.1.15.270115")
    assert profile.vm_vptr == 0x99A1170
    assert profile.editor_vptr == 0x999A3D0
    assert profile.view_vptr == 0x999A960
    assert profile.navigate == 0xA57524
    assert profile.send == 0x12FBF4
    assert profile.send_index == 8
    assert profile.send_action == 3


def test_unknown_build_is_rejected() -> None:
    with pytest.raises(UnsupportedBuild):
        profile_for("4.1.16.1")


def test_qstring_blob_layout() -> None:
    blob = qstring_blob("你好")
    assert blob[0:4] == b"\xff\xff\xff\xff"
    assert int.from_bytes(blob[4:8], "little") == 2
    assert int.from_bytes(blob[16:24], "little") == 24
    assert blob[24:-2] == "你好".encode("utf-16-le")
    assert blob[-2:] == bytes(2)


def test_send_happy_path() -> None:
    executor = FakeExecutor(
        [1]
    )
    outcome = _sender(executor).send("filehelper", "hello")
    assert outcome.peer == "filehelper"
    assert outcome.text == "hello"
    assert len(executor.evaluations) == 1


def test_send_refuses_existing_draft() -> None:
    executor = FakeExecutor([-2])
    with pytest.raises(DraftNotEmpty):
        _sender(executor).send("filehelper", "hello")
    assert len(executor.evaluations) == 1


def test_send_rejects_unselected_conversation() -> None:
    executor = FakeExecutor([-1])
    with pytest.raises(SendRejected):
        _sender(executor).send("filehelper", "hello")


def test_send_rejects_readback_mismatch() -> None:
    executor = FakeExecutor(
        [-3]
    )
    with pytest.raises(Exception):
        _sender(executor).send("filehelper", "hello")
    assert len(executor.evaluations) == 1


def test_clear_draft_skips_send_when_empty_text() -> None:
    executor = FakeExecutor(
        [1]
    )
    _sender(executor).clear_draft("filehelper")
    assert len(executor.evaluations) == 1


def test_select_peer_uses_probe_and_navigate() -> None:
    probe = FakeProbe()
    executor = FakeExecutor([1])
    assert _sender(executor, probe).select_peer("filehelper") is True
    assert probe.calls and probe.calls[0][1] == "filehelper"


def test_select_peer_returns_false_for_unknown_peer() -> None:
    executor = FakeExecutor([-1])
    assert _sender(executor).select_peer("ghost") is False
