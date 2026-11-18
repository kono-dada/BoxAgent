"""Capture while a game call is live and watch for a moment worth reacting to."""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from boxagent.application.game_entry import GameEntry
from boxagent.domain.proactivity.game_entry import GameCapture
from boxagent.domain.proactivity.game_highlight import (
    FOLLOWUP_WINDOW, FRAME_WINDOW, HIGHLIGHT_RECEIPT_PREFIX, REVIEW_DEADLINE,
    SCENE_MAX_AGE, GameFrame, GameHighlight, GameObservation, HighlightReview,
    game_scene_reference,
)

logger = logging.getLogger(__name__)
FRAME_PERIOD = .5
REVIEW_PERIOD = 2.0
PRAISE_COOLDOWN = 30.0
REVIEW_FRAMES = 6


class GameVision(Protocol):
    def foreground(self) -> int | None: ...
    def launch_id(self) -> str: ...
    async def capture(self, window: int) -> GameCapture: ...
    async def describe(self, frame: GameFrame) -> GameObservation | None: ...
    async def review(self, frames: tuple[GameFrame, ...]) -> HighlightReview: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class GameSource:
    voice: object
    window: int
    launch: str


class GameCompanion:
    def __init__(self, vision: GameVision, active_voice: Callable[[], object | None],
                 announce: Callable[[GameHighlight, Callable[[], bool]], Awaitable[bool]],
                 *, entry: GameEntry | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.vision = vision
        self.active_voice = active_voice
        self.announce = announce
        self.entry = entry
        self.clock = clock
        self.task: asyncio.Task[None] | None = None
        self.review_task: asyncio.Task[None] | None = None
        self.frames: deque[GameFrame] = deque(maxlen=16)
        self.source: GameSource | None = None
        self.next_review = 0.0
        self.next_description = 0.0
        self.described_since_review = False
        self.last_candidate_at: float | None = None
        self.armed = True
        self.pending: GameHighlight | None = None
        self.spoken: tuple[GameHighlight, float] | None = None
        self.observations: deque[GameObservation] = deque(maxlen=3)

    def _current_source(self) -> GameSource | None:
        voice = self.active_voice()
        window = self.vision.foreground() if voice is not None else None
        return GameSource(voice, window, self.vision.launch_id()) if voice is not None and window is not None else None

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="wuwa-companion")

    async def close(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._reset(None)
        if self.entry is not None:
            await self.entry.close()
        await self.vision.close()

    def _reset(self, source: GameSource | None) -> None:
        self.source = source
        if self.entry is not None:
            self.entry.pause()
        if self.review_task is not None:
            self.review_task.cancel()
            # Retain the task until it finishes so a cancelled model request
            # can release its worker before a replacement review starts.
        self.frames.clear()
        self.pending = None
        self.spoken = None
        self.observations.clear()
        self.next_review = 0.0
        self.next_description = 0.0
        self.described_since_review = False
        self.armed = True

    async def _run(self) -> None:
        try:
            while True:
                started = self.clock()
                try:
                    await self._tick(started)
                except (OSError, RuntimeError, TimeoutError):
                    self._reset(None)
                    logger.warning("Wuthering Waves capture unavailable", exc_info=True)
                    await asyncio.sleep(2)
                await asyncio.sleep(max(0, FRAME_PERIOD - (self.clock() - started)))
        finally:
            if self.review_task is not None:
                self.review_task.cancel()
                await asyncio.gather(self.review_task, return_exceptions=True)
                self.review_task = None

    async def _tick(self, now: float) -> None:
        source = self._current_source()
        if source != self.source:
            self._reset(source)
        source = self.source
        if source is None:
            return
        if self.pending is not None and now > self.pending.expires_at:
            self.pending = None
        if self.spoken is not None and now - self.spoken[1] > FOLLOWUP_WINDOW:
            self.spoken = None
        capture = await self.vision.capture(source.window)
        if self._current_source() != source:
            self._reset(None)
            return
        sampled_at = capture.captured_at if capture.captured_at is not None else now
        if self.entry is not None and source.launch:
            self.entry.observe(source.launch, capture.quest, sampled_at,
                               lambda: self.source is source and self._current_source() == source)
        self.frames.append(GameFrame(sampled_at, capture.image))
        while self.frames and now - self.frames[0].captured_at > FRAME_WINDOW:
            self.frames.popleft()
        if self.review_task is not None and not self.review_task.done():
            return
        if self.described_since_review and len(self.frames) >= REVIEW_FRAMES and now >= self.next_review:
            frames = tuple(self.frames)
            selected = tuple(frames[round(i * (len(frames) - 1) / (REVIEW_FRAMES - 1))]
                             for i in range(REVIEW_FRAMES))
            self.next_review = now + REVIEW_PERIOD
            self.described_since_review = False
            self.review_task = asyncio.create_task(self._review(source, selected), name="wuwa-review")
        elif now >= self.next_description:
            self.next_description = now + FRAME_PERIOD
            self.described_since_review = True
            self.review_task = asyncio.create_task(
                self._describe(source, self.frames[-1]), name="wuwa-describe")

    def _observe(self, observation: GameObservation | None) -> None:
        if observation is not None and (not self.observations
                                        or observation.last_seen_at > self.observations[-1].last_seen_at):
            self.observations.append(observation)

    async def _describe(self, source: GameSource, frame: GameFrame) -> None:
        try:
            observation = await self.vision.describe(frame)
            if (self.source is source and self._current_source() == source
                    and self.clock() - frame.captured_at <= SCENE_MAX_AGE):
                self._observe(observation)
        except Exception:
            logger.warning("Wuthering Waves scene description unavailable", exc_info=True)
            self.next_description = self.clock() + 5

    async def _review(self, source: GameSource, frames: tuple[GameFrame, ...]) -> None:
        latest = frames[-1].captured_at
        try:
            # A cold local model can finish loading for the next review.
            # The freshness check below discards any stale result.
            result = await self.vision.review(frames)
            now = self.clock()
            if self.source is not source or self._current_source() != source or now - latest >= REVIEW_DEADLINE:
                return
            self._observe(result.observation)
            previous = self.last_candidate_at
            if result.scene == "fighting" and (previous is None or frames[0].captured_at > previous):
                self.armed = True
            if result.scene != "settled" or not self.armed:
                return
            if previous is not None and now - previous < PRAISE_COOLDOWN:
                return
            self.armed = False
            self.last_candidate_at = now
            highlight = GameHighlight(HIGHLIGHT_RECEIPT_PREFIX + uuid4().hex, result, latest)
            self.pending = highlight

            def still_current() -> bool:
                return (self.source is source and self.clock() <= highlight.expires_at
                        and self._current_source() == source)

            accepted = await self.announce(highlight, still_current)
            if not accepted and self.pending is highlight:
                self.pending = None
            logger.info("Wuthering Waves highlight %s, review age %.0f ms",
                        "requested" if accepted else "skipped", (now - latest) * 1000)
        except Exception:
            self.pending = None
            logger.warning("Wuthering Waves clip review unavailable", exc_info=True)
            self.next_review = self.clock() + 5

    def playback_started(self, receipt_id: str) -> None:
        highlight = self.pending
        if highlight is None or highlight.receipt_id != receipt_id:
            return
        self.pending = None
        now = self.clock()
        if now <= highlight.expires_at and self.source == self._current_source():
            self.spoken = (highlight, now)
            logger.info("Wuthering Waves highlight playback, last frame to audio %.0f ms",
                        (now - highlight.latest_frame_at) * 1000)

    def reference(self) -> str:
        if self.source is None or self.source != self._current_source():
            return ""
        now = self.clock()
        context = game_scene_reference(tuple(self.observations), now)
        if self.spoken is not None:
            highlight, started = self.spoken
            if now - started <= FOLLOWUP_WINDOW:
                context += highlight.reference()
        return context
