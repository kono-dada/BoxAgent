from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import pytest

from boxagent.application.wechat.service import PeerResolutionError, WeChatService
from boxagent.domain.wechat.models import DeliveryCheck, DeliveryCursor, DraftSnapshot, GroupMember, SendOutcome
from boxagent.infrastructure.wechat.addresses import profile_for
from boxagent.infrastructure.wechat.database.reader import DatabaseReader
from boxagent.infrastructure.wechat.probe import LiveObjects
from boxagent.infrastructure.wechat.sender import NativeWeChatSender, SQLiteSendRequests
from .conftest import PEER
from typing import cast
import asyncio
from boxagent.agent.harness.wechat_tools import WeChatToolGateway
from boxagent.application.wechat.module import WeChatModule


class Transport:
    calls = 0
    fail = False
    def select_peer(self, peer: str) -> bool:
        return True
    def draft_for(self, peer: str) -> DraftSnapshot:
        return DraftSnapshot(0)
    def clear_draft(self, peer: str) -> None:
        pass
    def send(self, peer: str, text: str, *, dry_run: bool = False) -> SendOutcome:
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated interrupted native call")
        return SendOutcome(peer, text, submitted=not dry_run, dry_run=dry_run)
    def send_mention(self, peer: str, member: GroupMember, text: str, *, dry_run: bool = False) -> SendOutcome:
        return self.send(peer, text, dry_run=dry_run)


class History:
    def delivery_cursor(self, peer: str) -> DeliveryCursor:
        return DeliveryCursor((("db", 100),))
    def find_delivery(self, peer: str, text: str, *, after: DeliveryCursor,
                      timeout: float = 25, mention: str | None = None) -> DeliveryCheck:
        assert after.watermarks == (("db", 100),)
        return DeliveryCheck(101, 999, True)


def service(reader: DatabaseReader, tmp_path: Path, transport: Transport) -> WeChatService:
    return WeChatService(reader, History(), lambda: transport, SQLiteSendRequests(tmp_path / "requests.sqlite"))


def test_send_receipt_and_persistent_replay(reader: DatabaseReader, tmp_path: Path) -> None:
    transport = Transport()
    first = service(reader, tmp_path, transport).send("立信", "hello", request_id="one")
    second = service(reader, tmp_path, transport).send("立信", "hello", request_id="one")
    assert first.acknowledged and first.local_id == 101 and first == second
    assert transport.calls == 1
    with pytest.raises(ValueError, match="another message"):
        service(reader, tmp_path, transport).send("立信", "different", request_id="one")


def test_unknown_native_outcome_is_never_reexecuted(reader: DatabaseReader, tmp_path: Path) -> None:
    transport = Transport()
    transport.fail = True
    first = service(reader, tmp_path, transport).send(PEER, "hello", request_id="one")
    second = service(reader, tmp_path, transport).send(PEER, "hello", request_id="one")
    assert first.status == second.status == "outcome_unknown"
    assert transport.calls == 1


def test_existing_unfinished_request_does_not_send(reader: DatabaseReader, tmp_path: Path) -> None:
    # Simulate a crash after durable intent, before finish().
    import hashlib
    import json
    fingerprint = hashlib.sha256(json.dumps([reader.account_id, PEER, "hello", False, None], ensure_ascii=False).encode()).hexdigest()
    requests = SQLiteSendRequests(tmp_path / "requests.sqlite")
    assert requests.claim("one", fingerprint)[0]
    transport = Transport()
    outcome = service(reader, tmp_path, transport).send(PEER, "hello", request_id="one")
    assert outcome.status == "outcome_unknown" and transport.calls == 0


def test_ambiguous_name_returns_candidates_without_native_call(reader: DatabaseReader, tmp_path: Path) -> None:
    transport = Transport()
    with pytest.raises(PeerResolutionError) as caught:
        service(reader, tmp_path, transport).send("同名", "hello", request_id="one")
    assert len(caught.value.candidates) == 2 and transport.calls == 0


def test_dry_run_identity_cannot_be_reused_to_publish(reader: DatabaseReader, tmp_path: Path) -> None:
    transport = Transport()
    result = service(reader, tmp_path, transport).send(PEER, "hello", request_id="one", dry_run=True)
    assert result.status == "dry_run_passed" and not result.submitted
    with pytest.raises(ValueError):
        service(reader, tmp_path, transport).send(PEER, "hello", request_id="one", dry_run=False)
    assert transport.calls == 1


def test_generated_native_branches_compile_without_a_client() -> None:
    compiler = shutil.which("clang++")
    if compiler is None:
        pytest.skip("C++ compiler not installed")
    live = LiveObjects(0x10000, 0x20000, 0x30000, 0x40000, 0x40158)

    class Probe:
        def locate(self, base: int, peer: str, profile: object) -> LiveObjects:
            return live
    class Executor:
        def base_address(self) -> int:
            return 0x100000000
        def evaluate(self, expressions: dict[str, str]) -> dict[str, int | None]:
            raise AssertionError("Compilation must never execute a native call")

    sender = NativeWeChatSender(Probe(), Executor(), profile_for("4.1.15.270115"))
    expressions = [sender.expression(0x100000000, live, "filehelper", "中文🙂", operation=op, dry_run=dry)
                   for op in ("send", "clear", "select", "draft") for dry in (False, True)]
    expressions.append(sender.expression(0x100000000, live, "room@chatroom", "你好", operation="send",
                                         member=GroupMember("room@chatroom", "wxid_test", "老师", True)))
    source = "\n".join(f"long test{i}(){{return {expression};}}" for i, expression in enumerate(expressions))
    process = subprocess.run([compiler, "-std=c++17", "-fsyntax-only", "-x", "c++", "-"],
                             input=source, text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr


def test_tool_gateway_read_scope_cannot_call_send() -> None:
    module = cast(WeChatModule, object())
    gateway = WeChatToolGateway(module)
    assert "wechat_send" not in {item["name"] for item in gateway.tools()}
    with pytest.raises(ValueError, match="unavailable"):
        asyncio.run(gateway.call("wechat_send", {}))
