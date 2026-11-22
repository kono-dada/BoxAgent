"""Privacy-bounded local time context capture."""

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from boxagent.domain.environment import EnvironmentContext


def _timezone_name(now: datetime) -> str:
    configured = os.environ.get("TZ", "").strip().lstrip(":")
    if configured:
        return configured
    try:
        target = str(Path("/etc/localtime").resolve())
        marker = "/zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    return str(now.tzinfo or "UTC")


class LocalSystemEnvironmentProvider:
    """Capture only the local time facts needed for normal interactions."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None):
        self.clock = clock or (lambda: datetime.now().astimezone())

    def capture(self) -> EnvironmentContext:
        now = self.clock()
        if now.tzinfo is None:
            now = now.astimezone()
        weekdays = ("星期一", "星期二", "星期三", "星期四",
                    "星期五", "星期六", "星期日")
        return EnvironmentContext(
            captured_at=now.isoformat(timespec="seconds"),
            timezone=_timezone_name(now),
            weekday=weekdays[now.weekday()],
        )
