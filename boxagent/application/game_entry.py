"""Confirm visible quest progress and greet once per game launch."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import uuid4

from boxagent.domain.proactivity.game_entry import ENTRY_RECEIPT_PREFIX, EntryHistory, QuestProgress, TrackedQuest

logger = logging.getLogger(__name__)
GREETING_WINDOW = 8.0
MAX_SAMPLE_GAP = 3.0


class EntryStore(Protocol):
    def read(self) -> EntryHistory: ...
    def write(self, state: EntryHistory) -> None: ...


@dataclass(frozen=True)
class Greeting:
    launch: str
    receipt: str
    first_seen: float
    expires_at: float
    remembered: bool


class GameEntry:
    def __init__(self, store: EntryStore,
                 announce: Callable[[str, str, Callable[[], bool]], Awaitable[bool]],
                 *, clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.announce = announce
        self.clock = clock
        self.history = store.read()
        self.reading: tuple[str, TrackedQuest, float, float] | None = None
        self.greeting: Greeting | None = None
        self.task: asyncio.Task[None] | None = None
        self.submitted_launch = ""

    def pause(self) -> None:
        self.reading = None

    async def close(self) -> None:
        self.pause()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def _save(self, state: EntryHistory) -> None:
        try:
            self.store.write(state)
        except OSError:
            logger.warning("Could not save game progress", exc_info=True)
        self.history = state

    def observe(self, launch: str, quest: TrackedQuest | None, captured_at: float,
                still_visible: Callable[[], bool]) -> None:
        if self.greeting is not None and self.clock() > self.greeting.expires_at:
            self.greeting = None
        previous = self.reading
        if quest is None:
            self.pause()
            return
        if (previous is None or previous[:2] != (launch, quest)
                or not 0 < captured_at - previous[3] <= MAX_SAMPLE_GAP):
            self.reading = (launch, quest, captured_at, captured_at)
            logger.debug("wuwa.entry.visible launch=%s objective=%s capture=%.3f",
                         launch, quest.objective, captured_at)
            return
        first_seen = previous[2]
        self.reading = (launch, quest, first_seen, captured_at)
        current = self.history.current
        last_launch = self.history.previous if current and current.launch == launch else current
        if current is None or (current.launch, current.quest) != (launch, quest):
            self._save(EntryHistory(QuestProgress(launch, quest, time.time()), last_launch,
                                    self.history.greeted_launch))
            logger.info("wuwa.entry.progress launch=%s objective=%s first_seen=%.3f",
                        launch, quest.objective, first_seen)

        if (launch in (self.history.greeted_launch, self.submitted_launch)
                or (self.task is not None and not self.task.done())
                or self.clock() - first_seen > GREETING_WINDOW):
            return
        remembered = bool(last_launch and last_launch.launch != launch and last_launch.quest == quest)
        greeting = Greeting(launch, ENTRY_RECEIPT_PREFIX + uuid4().hex, first_seen,
                            first_seen + GREETING_WINDOW, remembered)
        prefix = "又来啦。上次咱们的任务目标就是" if remembered else "来啦。现在的任务目标是"
        line = f"{prefix}「{quest.objective}」。接着走吧？"

        def still_current() -> bool:
            reading = self.reading
            return (self.clock() <= greeting.expires_at and reading is not None
                    and reading[:3] == (launch, quest, first_seen) and still_visible())

        self.greeting = greeting
        self.task = asyncio.create_task(self._speak(greeting, line, still_current), name="wuwa-entry-greeting")

    async def _speak(self, greeting: Greeting, line: str, still_current: Callable[[], bool]) -> None:
        try:
            if await self.announce(greeting.receipt, line, still_current):
                self.submitted_launch = greeting.launch
                return
        except Exception:
            logger.warning("Wuthering Waves entry speech failed", exc_info=True)
        if self.greeting is greeting:
            self.greeting = None

    def playback_started(self, receipt: str) -> None:
        greeting = self.greeting
        if greeting is None or greeting.receipt != receipt:
            return
        self.greeting = None
        self.submitted_launch = greeting.launch
        self._save(replace(self.history, greeted_launch=greeting.launch))
        logger.info("wuwa.entry.playback launch=%s remembered=%s elapsed_ms=%.0f",
                    greeting.launch, greeting.remembered, (self.clock() - greeting.first_seen) * 1000)
