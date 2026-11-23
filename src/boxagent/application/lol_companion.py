"""Own the opt-in LOL poller and the lifetime of its commentary."""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from boxagent.domain.proactivity.lol import LolCompanionPolicy, LolMoment, LolRead, LolSnapshot

logger = logging.getLogger(__name__)
COMMENT_WINDOW_SECONDS = 3.0


class LolAnnouncement(Protocol):
    def __call__(self, moment: LolMoment, still_current: Callable[[], bool],
                 playback_started: Callable[[], None]) -> bool: ...


class LolCompanion:
    def __init__(self, read: Callable[[], LolRead], policy: LolCompanionPolicy,
                 announce: LolAnnouncement,
                 publish: Callable[[str, str], None], *, interval: float = 2) -> None:
        self.read = read
        self.policy = policy
        self.announce = announce
        self.publish = publish
        self.interval = interval
        self.task: asyncio.Task[None] | None = None
        self.enabled = False
        self.lock = asyncio.Lock()
        self.snapshot: LolSnapshot | None = None
        self.received_at = 0.0
        self.pending_moment: LolMoment | None = None

    def reference(self) -> str:
        snapshot = self.snapshot
        if not self.enabled or snapshot is None or time.monotonic() - self.received_at > 6:
            return ""
        return snapshot.reference()

    async def toggle(self) -> None:
        async with self.lock:
            await self._set_enabled(not self.enabled)

    async def set_enabled(self, enabled: bool) -> None:
        async with self.lock:
            await self._set_enabled(enabled)

    async def _set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        if enabled:
            self.enabled = True
            self.publish("waiting", "等待进入 LOL 对局")
            self.task = asyncio.create_task(self._watch(), name="boxagent-lol-companion")
        else:
            await self.close()

    async def close(self) -> None:
        self.enabled = False
        self.snapshot = None
        self.pending_moment = None
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.publish("off", "LOL 陪玩已关闭")

    async def poll(self) -> None:
        result = await asyncio.to_thread(self.read)
        if not self.enabled:
            return
        if result.snapshot is None:
            self.snapshot = None
            self.pending_moment = None
            self.publish("waiting" if result.status == "unavailable" else "invalid",
                         "等待进入 LOL 对局" if result.status == "unavailable"
                         else "无法读取 LOL 对局数据，稍后自动重试")
            return
        if self.snapshot is not None and result.snapshot.game_time < self.snapshot.game_time - 5:
            self.pending_moment = None
        self.snapshot = result.snapshot
        self.received_at = time.monotonic()
        if result.snapshot.game_time < 1 and not any(
                e.name == "GameStart" for e in result.snapshot.events):
            self.publish("loading", "LOL 正在加载，等待对局开始")
            return
        moment = self.policy.observe(result.snapshot, time.monotonic())
        self.publish("active", "LOL 陪玩中")
        if moment is None:
            return
        self.pending_moment = moment
        deadline = self.received_at + COMMENT_WINDOW_SECONDS

        def still_current() -> bool:
            return self.enabled and self.pending_moment is moment and time.monotonic() <= deadline

        def playback_started() -> None:
            self.policy.delivered(moment, time.monotonic())
            self.pending_moment = None
            self.publish("active", moment.name)

        if not self.announce(moment, still_current, playback_started):
            self.pending_moment = None
            self.publish("active", "LOL 陪玩中；请保持语音连接以接收提醒")

    async def _watch(self) -> None:
        while self.enabled:
            try:
                await self.poll()
            except Exception:
                logger.exception("LOL companion poll failed")
                self.publish("error", "LOL 陪玩暂时不可用，稍后自动重试")
            await asyncio.sleep(self.interval)
