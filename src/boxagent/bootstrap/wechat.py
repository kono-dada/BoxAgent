"""Composition root for the local WeChat capability; key capture is excluded."""
from __future__ import annotations

import hashlib
import plistlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from boxagent.agent.harness.wechat_tools import WeChatToolGateway
from boxagent.application.wechat.module import WeChatModule
from boxagent.application.wechat.monitor import WeChatMonitor
from boxagent.application.wechat.service import WeChatService
from boxagent.core.paths import RESOURCE_ROOT as ROOT
from boxagent.domain.events.contracts import EventSink
from boxagent.domain.wechat.contracts import WeChatCapability
from boxagent.infrastructure.wechat._private import private_dir
from boxagent.infrastructure.wechat.addresses import profile_for
from boxagent.infrastructure.wechat.database.keys import DatabaseKeys
from boxagent.infrastructure.wechat.database.reader import DatabaseReader
from boxagent.infrastructure.wechat.database.snapshot import SnapshotRepository
from boxagent.infrastructure.wechat.executor import LLDBExecutor
from boxagent.infrastructure.wechat.health import LocalWeChatHealth
from boxagent.infrastructure.wechat.listener import DatabaseMessageSource, JsonlEventSink
from boxagent.infrastructure.wechat.moments import NativeMomentsPublisher
from boxagent.infrastructure.wechat.probe import ReplicaProbe, replica_process
from boxagent.infrastructure.wechat.sender import NativeWeChatSender, SQLiteSendRequests

REPLICA_APP = Path.home() / 'Applications/WeChat-4.1.15-BoxAgent.app'
REPLICA_EXECUTABLE = REPLICA_APP / 'Contents/MacOS/WeChat'
SUPPORTED_BUILD = '4.1.15.270115'
DEFAULT_KEYS = ROOT / '.runtime/private/artifacts/wechat-key-20261113/derived-keys.json'

@dataclass(frozen=True)
class WeChatConfig:
    app: Path = REPLICA_APP
    db_root: Path | None = None
    key_report: Path | None = DEFAULT_KEYS
    data_dir: Path = ROOT / '.runtime/pet/wechat'
    self_wxid: str | None = None
    watched_peer: str | None = None
    interval: float = 0.5
    key_material: Mapping[str, str] | None = field(default=None, repr=False)
    lldb: str = '/usr/bin/lldb'


def account_root(config: WeChatConfig) -> Path:
    if config.db_root is not None:
        return config.db_root.expanduser().resolve(strict=True)
    base = Path.home() / 'Library/Containers/com.tencent.xinWeChatBoxAgent/Data/Documents/xwechat_files'
    matches = list(base.glob('*/db_storage'))
    if len(matches) != 1:
        raise ValueError('Exactly one account required; provide db_root explicitly')
    return matches[0]


def build_native_wechat_sender(pid: int, *, build: str = SUPPORTED_BUILD,
                               app: Path = REPLICA_APP, output: Path | None = None,
                               lldb: str = '/usr/bin/lldb') -> NativeWeChatSender:
    profile = profile_for(build)
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    actual = str(info.get('CFBundleShortVersionString'))+'.'+str(info.get('CFBundleVersion'))
    if actual != build:
        raise ValueError('Configured native profile does not match the actual app build')
    return NativeWeChatSender(ReplicaProbe(pid, (app / 'Contents/MacOS/WeChat').resolve()),
                             LLDBExecutor(pid, app=app, output=output, lldb=lldb), profile)


def build_wechat_module(config: WeChatConfig | None = None, *, event_sink: EventSink | None = None) -> WeChatModule:
    config = config or WeChatConfig()
    keys = DatabaseKeys(account_root(config), config.key_report, self_wxid=config.self_wxid,
                        material=config.key_material)
    directory = private_dir(config.data_dir / hashlib.sha256(keys.account_id.encode()).hexdigest()[:16])
    snapshots = SnapshotRepository(keys, directory / 'snapshots')
    reader = DatabaseReader(snapshots)

    def transport() -> NativeWeChatSender:
        pid, _executable = replica_process(config.app)
        return build_native_wechat_sender(pid, app=config.app, output=directory / 'native', lldb=config.lldb)

    requests = SQLiteSendRequests(directory / 'send-requests.sqlite')
    service = WeChatService(reader, reader, transport, requests)

    def source() -> DatabaseMessageSource:
        peer = service.resolve(config.watched_peer).peer if config.watched_peer else None
        name = hashlib.sha256((peer or 'all').encode()).hexdigest()[:16]
        return DatabaseMessageSource(snapshots, directory / 'listeners' / name, peer=peer)

    monitor = WeChatMonitor(source, event_sink, interval=config.interval)
    return WeChatModule(service, monitor, NativeMomentsPublisher(config.app, directory / 'native/moments'),
                        LocalWeChatHealth(keys, config.app))


def build_local_wechat_module(config: WeChatConfig | None = None) -> WeChatModule:
    config = config or WeChatConfig()
    sink = JsonlEventSink(config.data_dir / 'local-events.jsonl')
    return build_wechat_module(config, event_sink=sink)


def build_wechat_tools(module: WeChatCapability, *, allow_write: bool = False) -> WeChatToolGateway:
    return WeChatToolGateway(module, allow_write=allow_write)
