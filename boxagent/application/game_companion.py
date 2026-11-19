"""Track foreground game progress and watch for highlights during a call."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from boxagent.application.game_entry import MAX_SAMPLE_GAP, GameEntry
from boxagent.domain.proactivity.game_entry import GameCapture, TrackedQuest
from boxagent.domain.proactivity.game_highlight import (
    FOLLOWUP_WINDOW, FRAME_WINDOW, HIGHLIGHT_RECEIPT_PREFIX, REVIEW_DEADLINE,
    SCENE_MAX_AGE, GameFrame, GameHighlight, GameObservation, HighlightReview,
    game_scene_reference,
)

logger = logging.getLogger(__name__)
FRAME_PERIOD = .5
QUEST_PERIOD = 1.0
REVIEW_PERIOD = 2.0
PRAISE_COOLDOWN = 30.0
REVIEW_FRAMES = 6


class GameVision(Protocol):
    def visible_window(self) -> int | None: ...
    def launch_id(self) -> str: ...
    async def capture(self, window: int) -> GameCapture: ...
    async def describe(self, frame: GameFrame) -> GameObservation | None: ...
    async def review(self, frames: tuple[GameFrame, ...]) -> HighlightReview: ...
    async def still_relevant(self, review: HighlightReview, frame: GameFrame) -> bool: ...
    async def pause_capture(self) -> None: ...
    async def close(self) -> None: ...


@dataclass(frozen=True)
class GameSource:
    voice: object | None
    window: int
    launch: str

    def same_window(self, other: GameSource | None) -> bool:
        return other is not None and (self.window, self.launch) == (other.window, other.launch)


class GameCompanion:
    def __init__(self, vision: GameVision, active_voice: Callable[[], object | None],
                 announce: Callable[[GameHighlight, Callable[[], bool]], Awaitable[bool]],
                 *, entry: GameEntry | None = None,
                 recognize_quest: Callable[[bytes], Awaitable[TrackedQuest | None]] | None = None,
                 cloud_scene: bool = False,
                 prepare_audio: Callable[[], Awaitable[None]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.vision = vision
        self.active_voice = active_voice
        self.announce = announce
        self.entry = entry
        self.recognize_quest = recognize_quest
        self.cloud_scene = cloud_scene
        self.prepare_audio = prepare_audio
        self.clock = clock
        self.task: asyncio.Task[None] | None = None
        self.review_task: asyncio.Task[None] | None = None
        self.quest_task: asyncio.Task[None] | None = None
        self.audio_task: asyncio.Task[None] | None = None
        self.next_audio = 0.0
        self.quest_generation = 0
        self.next_quest = 0.0
        self.quest_failed = False
        self.frames: deque[GameFrame] = deque(maxlen=16)
        self.source: GameSource | None = None
        self.next_review = 0.0
        self.next_description = 0.0
        self.described_since_review = False
        self.last_candidate_at: float | None = None
        self.last_entry_at: float | None = None
        self.last_moment = ""
        self.armed = True
        self.pending: GameHighlight | None = None
        self.spoken: tuple[GameHighlight, float] | None = None
        self.observations: deque[GameObservation] = deque(maxlen=3)

    def _current_source(self) -> GameSource | None:
        voice = self.active_voice()
        if voice is None and self.entry is None and not self.cloud_scene:
            return None
        window = self.vision.visible_window()
        if window is None:
            return None
        # Cloud observations belong to the game window, even before a voice call starts.
        return GameSource(None if self.cloud_scene else voice, window, self.vision.launch_id())

    async def start(self) -> None:
        if self.task is None:
            logger.info("wuwa.companion.start reader=%s scene=%s",
                        "cloud" if self.recognize_quest else "local", "cloud" if self.cloud_scene else "local")
            self.task = asyncio.create_task(self._run(), name="wuwa-companion")

    async def close(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._reset(None)
        if self.quest_task is not None:
            self.quest_task.cancel()
            await asyncio.gather(self.quest_task, return_exceptions=True)
            self.quest_task = None
        if self.audio_task is not None:
            self.audio_task.cancel()
            await asyncio.gather(self.audio_task, return_exceptions=True)
            self.audio_task = None
        if self.entry is not None:
            await self.entry.close()
        await self.vision.close()

    def _reset(self, source: GameSource | None) -> None:
        if self.entry is not None and (source is None or not source.same_window(self.source)):
            if source is not None:
                logger.info("wuwa.companion.window launch=%s window=%s", source.launch, source.window)
            self.entry.pause()
            self.quest_generation += 1
            self.next_quest = 0.0
            self.next_audio = 0.0
        self.source = source
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
            await self.vision.pause_capture()
            return
        if (self.active_voice() is None and self.prepare_audio is not None and now >= self.next_audio
                and (self.audio_task is None or self.audio_task.done())):
            self.next_audio = now + 20.0
            self.audio_task = asyncio.create_task(self._prepare_audio(self.prepare_audio), name="wuwa-audio")
        if self.pending is not None and now > self.pending.expires_at:
            self.pending = None
        if self.spoken is not None and now - self.spoken[1] > FOLLOWUP_WINDOW:
            self.spoken = None
        capture = await self.vision.capture(source.window)
        current = self._current_source()
        if current is None or not source.same_window(current):
            self._reset(None)
            return
        if current != source:
            self._reset(current)
            source = current
        sampled_at = capture.captured_at if capture.captured_at is not None else now
        if self.entry is not None and source.launch:
            self._record_quest_frame(source, capture, sampled_at)
        if source.voice is None and not self.cloud_scene:
            return
        self.frames.append(GameFrame(sampled_at, capture.image, capture.scene_signature))
        while self.frames and now - self.frames[0].captured_at > FRAME_WINDOW:
            self.frames.popleft()
        if self.review_task is not None and not self.review_task.done():
            return
        if self.entry is not None and self.entry.opening_pending(source.launch):
            return
        if self.cloud_scene:
            if now >= self.next_review:
                self.next_review = now + 3.0
                self.review_task = asyncio.create_task(self._review(source, tuple(self.frames)[-3:]), name="wuwa-scene")
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

    @staticmethod
    async def _prepare_audio(prepare: Callable[[], Awaitable[None]]) -> None:
        try:
            await prepare()
        except Exception:
            logger.warning("Wuthering Waves speaker connection unavailable", exc_info=True)

    def _record_quest_frame(self, source: GameSource, capture: GameCapture, sampled_at: float) -> None:
        if self.recognize_quest is None:
            self._observe_quest(source, capture.quest, sampled_at)
            return
        if (capture.quest_image is None or sampled_at < self.next_quest
                or (self.quest_task is not None and not self.quest_task.done())):
            return
        self.next_quest = sampled_at + QUEST_PERIOD
        self.quest_task = asyncio.create_task(
            self._read_quest(self.recognize_quest, source, capture.quest_image, sampled_at, self.quest_generation),
            name="wuwa-quest")

    async def _read_quest(
        self, recognize: Callable[[bytes], Awaitable[TrackedQuest | None]], source: GameSource,
        image: bytes, sampled_at: float, generation: int,
    ) -> None:
        try:
            quest = await recognize(image)
        except (OSError, RuntimeError, ValueError):
            if not self.quest_failed:
                logger.warning("Wuthering Waves quest recognition unavailable", exc_info=True)
                self.quest_failed = True
            quest = None
        else:
            self.quest_failed = False
        current = self.source
        if (current is not None and generation == self.quest_generation and source.same_window(current)
                and source.same_window(self._current_source()) and self.clock() - sampled_at <= MAX_SAMPLE_GAP):
            self._observe_quest(current, quest, sampled_at)

    def _observe_quest(self, source: GameSource, quest: TrackedQuest | None, sampled_at: float) -> None:
        if self.entry is not None:
            self.entry.observe(source.launch, quest, sampled_at,
                               lambda: self.source is source and self._current_source() == source,
                               can_announce=source.voice is not None or self.prepare_audio is not None)

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
            highlight = GameHighlight(HIGHLIGHT_RECEIPT_PREFIX + uuid4().hex, result, latest)
            if self.source is not source or self._current_source() != source or now >= highlight.expires_at:
                return
            self._observe(result.observation)
            if self.entry is not None and self.entry.opening_pending(source.launch):
                return
            previous = self.last_candidate_at
            if result.scene == "fighting" and (previous is None or frames[0].captured_at > previous):
                self.armed = True
            is_moment = self.cloud_scene and result.scene == "moment"
            if is_moment and result.praise == self.last_moment:
                return
            if not is_moment and (result.scene != "settled" or not self.armed):
                return
            if previous is not None and now - previous < PRAISE_COOLDOWN:
                return
            if not is_moment:
                self.armed = False
                self.last_candidate_at = now
            verified = frames[-1]
            if self.cloud_scene and result.timing != "recollection":
                capture = await self.vision.capture(source.window)
                verified = GameFrame(
                    capture.captured_at if capture.captured_at is not None else self.clock(),
                    capture.image, capture.scene_signature)
                if not await self.vision.still_relevant(result, verified):
                    logger.info("game.comment.discarded reason=scene_changed")
                    return
                # The capture loop may have moved on while cloud verification was running.
                now = self.clock()
            self.pending = highlight
            entry_spoken_at = self.last_entry_at

            def still_current() -> bool:
                newest = self.frames[-1] if self.frames else None
                return (self.source is source and self.clock() <= highlight.expires_at
                        and self._current_source() == source and newest is not None
                        and (self.entry is None or not self.entry.opening_pending(source.launch))
                        and self.last_entry_at == entry_spoken_at
                        and 0 <= self.clock() - newest.captured_at <= 1.5
                        and (not self.cloud_scene or result.timing == "recollection"
                             or (self.clock() - verified.captured_at <= 3
                                 and verified.same_scene(newest))))

            if not still_current():
                self.pending = None
                return
            accepted = await self.announce(highlight, still_current)
            if is_moment and accepted:
                self.last_candidate_at = now
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
            self.last_moment = highlight.review.praise
            logger.info("Wuthering Waves highlight playback, last frame to audio %.0f ms",
                        (now - highlight.latest_frame_at) * 1000)

    def entry_playback_started(self, receipt_id: str) -> None:
        if self.entry is None or self.entry.greeting is None or self.entry.greeting.receipt != receipt_id:
            return
        self.entry.playback_started(receipt_id)
        self.last_candidate_at = self.clock()
        self.last_entry_at = self.last_candidate_at

    def reference(self) -> str:
        if self.source is None or self.source != self._current_source():
            return ""
        now = self.clock()
        context = (self.entry.reference(self.source.launch) if self.entry is not None else "")
        context += game_scene_reference(tuple(self.observations), now)
        if self.spoken is not None:
            highlight, started = self.spoken
            if now - started <= FOLLOWUP_WINDOW:
                context += highlight.reference()
        return context

    def is_active(self) -> bool:
        """Use the capture loop's fresh state without extra native window polling."""
        return bool(self.source is not None and self.frames
                    and 0 <= self.clock() - self.frames[-1].captured_at <= 1.5)
