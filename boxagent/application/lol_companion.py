"""Own the opt-in LOL poller and drop commentary that cannot be delivered now."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from boxagent.domain.proactivity.lol import LolCompanionPolicy, LolMoment, LolRead, LolSnapshot

logger = logging.getLogger(__name__)


class LolCompanion:
    def __init__(self, read: Callable[[], LolRead], policy: LolCompanionPolicy,
                 announce: Callable[[LolMoment], Awaitable[bool]],
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

    def reference(self) -> str:
        snapshot = self.snapshot
        if not self.enabled or snapshot is None or time.monotonic() - self.received_at > 6:
            return ""
        return snapshot.reference()

    async def toggle(self) -> None:
        async with self.lock:
            if self.enabled:
                await self.close()
            else:
                self.enabled = True
                self.publish("waiting", "等待进入 LOL 对局")
                self.task = asyncio.create_task(self._watch(), name="boxagent-lol-companion")

    async def close(self) -> None:
        self.enabled = False
        self.snapshot = None
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
            self.publish("waiting" if result.status == "unavailable" else "invalid",
                         "等待进入 LOL 对局" if result.status == "unavailable"
                         else "无法读取 LOL 对局数据，稍后自动重试")
            return
        self.snapshot = result.snapshot
        self.received_at = time.monotonic()
        if result.snapshot.game_time < 1 and not any(
                e.name == "GameStart" for e in result.snapshot.events):
            self.publish("loading", "LOL 正在加载，等待对局开始")
            return
        moment = self.policy.observe(result.snapshot, time.monotonic())
        self.publish("active", "LOL 陪玩中")
        if moment is not None:
            if await self.announce(moment):
                self.policy.delivered(moment, time.monotonic())
                self.publish("active", moment.name)
            else:
                self.publish("active", "LOL 陪玩中；请保持语音连接，忙时跳过提醒")

    async def _watch(self) -> None:
        while self.enabled:
            try:
                await self.poll()
            except Exception:
                logger.exception("LOL companion poll failed")
                self.publish("error", "LOL 陪玩暂时不可用，稍后自动重试")
            await asyncio.sleep(self.interval)
