"""First-run workflow; host owns dialogs and encrypted credential persistence."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypedDict


class Check(TypedDict):
    state: str
    code: str


class SetupResult(TypedDict, total=False):
    client: Check
    reading: Check
    listening: Check
    sending: Check
    app: str
    root: str
    material: dict[str, str]


class PrepareClient(Protocol):
    def __call__(self, source: Path, destination: Path) -> Path: ...


class AcquireKeys(Protocol):
    def __call__(self, app: Path, root: Path | None, *, lldb: Path,
                 cancelled: Callable[[], bool]) -> tuple[Path, dict[str, str]]: ...


class VerifyAccount(Protocol):
    def __call__(self, app: Path, root: Path, material: Mapping[str, str], *,
                 lldb: Path, native: bool) -> SetupResult: ...


@dataclass
class WeChatOnboarding:
    prepare_client: PrepareClient
    check_client: Callable[[Path], None]
    acquire_keys: AcquireKeys
    verify_account: VerifyAccount

    def prepare(self, source: Path, destination: Path) -> SetupResult:
        app = self.prepare_client(source, destination)
        return {'app': str(app), 'client': {'state': 'ready', 'code': ''}}

    def capture(self, app: Path, root: Path | None, *, lldb: Path,
                cancelled: Callable[[], bool]) -> SetupResult:
        self.check_client(app)
        root, material = self.acquire_keys(app, root, lldb=lldb, cancelled=cancelled)
        result = self.verify_account(app, root, material, lldb=lldb, native=False)
        if any(result.get(name, {'state': 'missing', 'code': ''})['state'] != 'ready'
               for name in ('reading', 'listening')):
            raise RuntimeError('wechat_validation_failed')
        result.update(app=str(app), root=str(root), material=material)
        return result

    def verify(self, app: Path, root: Path, material: Mapping[str, str], *,
               lldb: Path, native: bool = False) -> SetupResult:
        self.check_client(app)
        return self.verify_account(app, root, material, lldb=lldb, native=native)
