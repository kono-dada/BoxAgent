"""Assemble onboarding using the same account, snapshot, listener and send adapters."""
from __future__ import annotations

import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from boxagent.application.wechat.onboarding import SetupResult, WeChatOnboarding
from boxagent.bootstrap.wechat import WeChatConfig, build_wechat_module
from boxagent.infrastructure.wechat.database.capture import capture_passphrase, derive_material
from boxagent.infrastructure.wechat.database.keys import DatabaseKeys
from boxagent.infrastructure.wechat.database.reader import DatabaseReader
from boxagent.infrastructure.wechat.database.snapshot import SnapshotRepository
from boxagent.infrastructure.wechat.listener import DatabaseMessageSource
from boxagent.infrastructure.wechat.replica import check_client, prepare_client


def acquire_keys(app: Path, root: Path | None, *, lldb: Path,
                 cancelled: Callable[[], bool]) -> tuple[Path, dict[str, str]]:
    # Launch this exact executable stopped at entry, before any login KDF.
    # Never wait for an ambiguous process name or terminate the user's client.
    passphrase = capture_passphrase(app, lldb=lldb, cancelled=cancelled, launch=True)
    deadline = time.monotonic() + 30
    last_error: RuntimeError | None = None
    while not cancelled() and time.monotonic() < deadline:
        if root is None:
            parent = (
                Path.home()
                / "Library/Containers/com.tencent.xinWeChatBoxAgent/Data/Documents/xwechat_files"
            )
            candidates = list(parent.glob("*/db_storage"))
            if len(candidates) > 1:
                raise ValueError('wechat_choose_account')
            candidate = candidates[0] if candidates else None
        else:
            candidate = root
        if candidate is not None and (candidate/'session/session.db').is_file():
            try:
                return candidate.resolve(strict=True), derive_material(passphrase, candidate)
            except FileNotFoundError:
                pass  # The login has not created every shard yet.
            except RuntimeError as error:
                if str(error) not in ('wechat_databases_missing', 'wechat_keys_invalid'):
                    raise
                last_error = error  # Initial encrypted pages may still be being created.
        time.sleep(.2)
    if cancelled():
        raise RuntimeError('cancelled')
    raise last_error or RuntimeError('wechat_databases_missing')


def verify_account(app: Path, root: Path, material: Mapping[str, str], *,
                   lldb: Path, native: bool) -> SetupResult:
    keys = DatabaseKeys(root, material=material)
    for relative in ('contact/contact.db', 'session/session.db', *keys.messages()):
        keys.key(relative)
    # Disposable local baselines do not modify the live listener's durable cursor.
    with tempfile.TemporaryDirectory(prefix='boxagent-wechat-check-') as temporary:
        directory = Path(temporary)
        snapshots = SnapshotRepository(keys, directory/'snapshots')
        reader = DatabaseReader(snapshots)
        reader.contacts(limit=1)
        reader.sessions(limit=1)
        source = DatabaseMessageSource(snapshots, directory/'listener')
        try:
            source.poll()
            source.poll()
        finally:
            source.close()
        result: SetupResult = {
            'client': {'state': 'ready', 'code': ''},
            'reading': {'state': 'ready', 'code': ''},
            'listening': {'state': 'ready', 'code': ''},
            'sending': {'state': 'missing', 'code': 'wechat_native_check_required'},
        }
        if native:
            module = build_wechat_module(
                WeChatConfig(
                    app=app,
                    db_root=root,
                    key_report=None,
                    key_material=material,
                    data_dir=directory / "native-check",
                    lldb=str(lldb),
                )
            )
            outcome = module.send(
                "filehelper",
                "BoxAgent connection check",
                request_id="setup-" + uuid.uuid4().hex,
                dry_run=True,
            )
            result["sending"] = (
                {"state": "ready", "code": "wechat_dry_run_only"}
                if outcome.dry_run and not outcome.submitted and not outcome.error_code
                else {"state": "unavailable", "code": "wechat_open_main_window_or_clear_draft"}
            )
        return result


def build_wechat_onboarding() -> WeChatOnboarding:
    return WeChatOnboarding(prepare_client, check_client, acquire_keys, verify_account)
