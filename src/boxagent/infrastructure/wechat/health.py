"""Capability checks use actual configured sources, never an onboarding flag."""
from __future__ import annotations

import logging
from pathlib import Path
import plistlib
import shutil
import sys

from boxagent.domain.wechat.models import Capability, WeChatHealth
from boxagent.infrastructure.wechat.addresses import BUILD, BUNDLE_ID, VERSION
from boxagent.infrastructure.wechat.database.keys import DatabaseKeys
from boxagent.infrastructure.wechat.probe import replica_process
from boxagent.infrastructure.darwin.mach_reader import TaskReadError
from boxagent.domain.wechat.contracts import OperationRejected

logger = logging.getLogger(__name__)


class LocalWeChatHealth:
    def __init__(self, keys: DatabaseKeys, app: Path) -> None:
        self.keys = keys
        self.app = app

    def health(self) -> WeChatHealth:
        reading = Capability("ready")
        try:
            for relative in ("contact/contact.db", "session/session.db", *self.keys.messages()):
                self.keys.key(relative)
        except (OSError, ValueError) as error:
            logger.info("WeChat read health failed: %s", type(error).__name__)
            reading = Capability("unavailable", type(error).__name__)
        native = Capability("experimental", "Build checked; live identity/composer guards run on each operation")
        try:
            info = plistlib.loads((self.app / "Contents/Info.plist").read_bytes())
            if (info.get("CFBundleIdentifier"), info.get("CFBundleShortVersionString"), str(info.get("CFBundleVersion"))) != (BUNDLE_ID, VERSION, BUILD):
                raise ValueError("unsupported_build")
            if sys.platform != "darwin" or shutil.which("lldb") is None:
                raise ValueError("native_executor_unavailable")
            replica_process(self.app)
        except (OSError, ValueError, OperationRejected, TaskReadError) as error:
            native = Capability("unavailable", str(error))
        moments = (Capability("experimental", "Private text tested; submission confirmation and async-root cleanup remain limited")
                   if native.state != "unavailable" else native)
        return WeChatHealth(self.keys.account_id, reading, reading, native, moments)
