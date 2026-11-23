from __future__ import annotations

import hashlib
import hmac
import plistlib
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from boxagent.application.wechat.onboarding import WeChatOnboarding
from boxagent.infrastructure.wechat.database.capture import (
    KeyCaptureError,
    derive_material,
    parse_capture,
)
from boxagent.infrastructure.wechat.database.keys import DatabaseKeys
from boxagent.infrastructure.wechat.distribution import build_client_archive, verify_archive
from boxagent.infrastructure.wechat.replica import (
    check_client,
    prepare_client,
    replica_entitlements,
)


def encrypted_page(passphrase: bytes, salt: bytes) -> bytes:
    key = hashlib.pbkdf2_hmac('sha512', passphrase, salt, 256000, 32)
    mac = hashlib.pbkdf2_hmac('sha512', key, bytes(x ^ 0x3a for x in salt), 2, 32)
    body = bytes(4096-16-64)
    return salt + body + hmac.digest(mac, body+b'\x01\x00\x00\x00', 'sha512')


def test_capture_derive_authenticate_account_and_reject_other_account(tmp_path: Path) -> None:
    secret = bytes(range(32))
    root = tmp_path/'wxid_fixture_abcd/db_storage'
    names = ('contact/contact.db', 'session/session.db', 'message/message_0.db')
    for i, name in enumerate(names):
        file = root/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(encrypted_page(secret, bytes([i+1])*16))
    material = derive_material(secret, root)
    keys = DatabaseKeys(root, material=material)
    assert keys.account_id == 'wechat:wxid_fixture'
    assert all(keys.key(name) == bytes.fromhex(material[name]) for name in names)
    (root/names[0]).write_bytes(encrypted_page(bytes([99])*32, bytes([1])*16))
    with pytest.raises(ValueError, match='does not authenticate'):
        keys.key(names[0])
    with pytest.raises(KeyCaptureError, match='wechat_keys_invalid'):
        derive_material(secret, root)


def test_capture_parser_requires_complete_key_and_salt() -> None:
    dump = "\n".join(
        f"0x{0x1000 + i:x}: " + " ".join(f"0x{j:02x}" for j in range(i, i + 16))
        for i in (0, 16, 32)
    )
    assert parse_capture(dump) == bytes(range(32))
    assert parse_capture(dump.splitlines()[0]) is None
    assert parse_capture('error: attach failed') is None


def test_prepare_rejects_wrong_build_before_copying(tmp_path: Path) -> None:
    source = tmp_path/'WeChat.app'
    (source/'Contents').mkdir(parents=True)
    (source/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleShortVersionString': '4.1.15', 'CFBundleVersion': '270135'}))
    destination = tmp_path/'copy.app'
    with pytest.raises(ValueError, match='wechat_unsupported_build'):
        prepare_client(source, destination)
    assert not destination.exists()


def test_client_checks_real_resources_image_not_loading_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxagent.infrastructure.wechat import replica
    app = tmp_path/'WeChat.app'
    (app/'Contents').mkdir(parents=True)
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleShortVersionString': '4.1.15', 'CFBundleVersion': '270115'}))
    image = Mock()
    monkeypatch.setattr(replica, "disk_image", image)
    monkeypatch.setattr(
        replica.subprocess, "run", Mock(return_value=Mock(stderr=b"TeamIdentifier=5A4RE8SF68"))
    )
    check_client(app, replica=False)
    image.assert_called_once_with(app/'Contents/Resources/wechat.dylib')


def test_capture_cannot_return_keys_before_both_read_and_listen_checks_pass(tmp_path: Path) -> None:
    material = {'session/session.db': 'ab'*32}
    acquire = Mock(return_value=(tmp_path, material))
    verify = Mock(return_value={'reading': {'state': 'ready', 'code': ''}})
    setup = WeChatOnboarding(Mock(), Mock(), acquire, verify)
    with pytest.raises(RuntimeError, match='wechat_validation_failed'):
        setup.capture(tmp_path, None, lldb=tmp_path/'lldb', cancelled=lambda: False)
    assert verify.call_args.kwargs['native'] is False


def test_capture_launch_never_launches_when_replica_is_already_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxagent.infrastructure.wechat.database import capture
    executable = tmp_path/'Contents/MacOS/WeChat'
    executable.parent.mkdir(parents=True)
    executable.touch()
    lldb = tmp_path/'lldb'
    lldb.write_text('')
    lldb.chmod(0o700)
    monkeypatch.setattr(capture, 'check_client', lambda _: None)
    monkeypatch.setattr(capture, 'matching_pids', lambda _: (123,))
    launch = Mock()
    monkeypatch.setattr(capture, '_capture_pid', launch)
    with pytest.raises(KeyCaptureError, match='wechat_close_replica_first'):
        capture.capture_passphrase(tmp_path, lldb=lldb, cancelled=lambda: False, launch=True)
    launch.assert_not_called()


def test_archive_creation_rejects_other_build_before_staging(tmp_path: Path) -> None:
    source = tmp_path/'WeChat.app'
    (source/'Contents').mkdir(parents=True)
    (source/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleShortVersionString': '4.1.15', 'CFBundleVersion': '270135'}))
    with pytest.raises(ValueError, match='wechat_unsupported_build'):
        build_client_archive(source, tmp_path/'archive')
    assert not (tmp_path/'archive').exists()


def test_failed_archive_detach_never_deletes_the_mounted_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxagent.infrastructure.wechat import distribution
    base = tmp_path/'mount-check'
    base.mkdir()
    monkeypatch.setattr(distribution.tempfile, 'mkdtemp', lambda **_: str(base))
    def run(arguments: list[str], **_: object) -> Mock:
        if 'attach' in arguments:
            app = base/'volume/WeChat.app'
            app.mkdir()
            (app/'keep').write_text('must survive')
            return Mock(returncode=0)
        return Mock(returncode=1)
    monkeypatch.setattr(distribution.subprocess, 'run', run)
    monkeypatch.setattr(distribution, 'check_client', lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match='wechat_archive_detach_failed'):
        verify_archive(tmp_path/'source.dmg')
    assert (base/'volume/WeChat.app/keep').read_text() == 'must survive'


def test_login_waits_for_initial_encrypted_pages_instead_of_recapturing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxagent.bootstrap import wechat_onboarding
    root = tmp_path/'db_storage'
    (root/'session').mkdir(parents=True)
    (root/'session/session.db').touch()
    material = {'session/session.db': 'ab'*32}
    capture = Mock(return_value=bytes(range(32)))
    derive = Mock(side_effect=[KeyCaptureError('wechat_keys_invalid'), material])
    monkeypatch.setattr(wechat_onboarding, 'capture_passphrase', capture)
    monkeypatch.setattr(wechat_onboarding, 'derive_material', derive)
    monkeypatch.setattr(wechat_onboarding.time, 'sleep', lambda _: None)
    found_root, found = wechat_onboarding.acquire_keys(tmp_path/'replica.app', root,
        lldb=tmp_path/'lldb', cancelled=lambda: False)
    assert found_root == root
    assert found == material
    capture.assert_called_once()
    assert derive.call_count == 2


def test_replica_removes_restricted_team_claim_and_preserves_sandbox_isolation() -> None:
    original: dict[str, object] = {
        'com.apple.developer.team-identifier': '5A4RE8SF68',
        'com.apple.application-identifier': '5A4RE8SF68.com.tencent.xinWeChat',
        'com.apple.security.app-sandbox': True,
        'com.apple.security.application-groups': ['5A4RE8SF68.com.tencent.xinWeChat'],
    }
    result = replica_entitlements(plistlib.dumps(original))
    assert "com.apple.developer.team-identifier" not in result
    assert result["com.apple.security.app-sandbox"] is True
    assert result["com.apple.security.get-task-allow"] is True
    assert result["com.apple.application-identifier"] == "5A4RE8SF68.com.tencent.xinWeChatBoxAgent"
    assert result["com.apple.security.application-groups"] == [
        "5A4RE8SF68.com.tencent.xinWeChatBoxAgent"
    ]
    assert replica_entitlements(plistlib.dumps(result)) == result


def test_launch_failure_is_not_reported_as_waiting_for_login(tmp_path: Path) -> None:
    from boxagent.infrastructure.wechat.database.capture import _capture_pid

    executable = tmp_path / "fake-lldb"
    executable.write_text(
        "#!/bin/sh\necho 'error: process exited with status -1 (no such process)'\nsleep 1\n"
    )
    executable.chmod(0o700)
    with pytest.raises(KeyCaptureError, match='wechat_client_launch_failed'):
        _capture_pid(0, executable, cancelled=lambda: False, deadline=time.monotonic()+5,
                     executable=tmp_path/'WeChat')
