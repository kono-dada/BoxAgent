"""Bounded foreground-window capture and local vision for Wuthering Waves."""

import asyncio
import logging
import time
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw

from boxagent.domain.proactivity.game_entry import GameCapture
from boxagent.domain.proactivity.game_highlight import (
    REVIEW_PROMPT, SCENE_PROMPT, GameFrame, GameObservation, HighlightReview,
)
from boxagent.infrastructure.perception.qwen_mlx import WindowSummary, clean_summary, focused_window
from boxagent.infrastructure.perception.wuwa_quest import read_quest

WUWA_BUNDLE = "com.kurogame.mingchao"
TILE_WIDTH = 512
TILE_HEIGHT = 320
logger = logging.getLogger(__name__)


def game_contact_sheet(frames: tuple[GameFrame, ...]) -> bytes:
    columns = 2
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * TILE_WIDTH, rows * TILE_HEIGHT), (16, 16, 16))
    draw = ImageDraw.Draw(sheet)
    for index, frame in enumerate(frames):
        with Image.open(BytesIO(frame.image)) as image:
            image.thumbnail((TILE_WIDTH - 8, TILE_HEIGHT - 27))
            x = (index % columns) * TILE_WIDTH
            y = (index // columns) * TILE_HEIGHT
            sheet.paste(image, (x + (TILE_WIDTH - image.width) // 2, y + 22))
            elapsed = frame.captured_at - frames[-1].captured_at
            draw.text((x + 8, y + 4), f"Frame {index + 1}   {elapsed:.1f}s", fill="white")
    with BytesIO() as encoded:
        sheet.save(encoded, "JPEG", quality=88)
        return encoded.getvalue()


class WuwaVision:
    def __init__(self, local_model: WindowSummary) -> None:
        self.local_model = local_model
        self.quest_error_reported = False

    def foreground(self) -> int | None:
        window = focused_window(bundle_id=WUWA_BUNDLE)
        return window["id"] if window is not None else None

    def launch_id(self) -> str:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None or app.bundleIdentifier() != WUWA_BUNDLE:
            return ""
        launched = app.launchDate()
        return f"{app.processIdentifier()}:{launched.timeIntervalSince1970()}" if launched else ""

    async def capture(self, window: int) -> GameCapture:
        with TemporaryDirectory(prefix="boxagent-game-frame-") as directory:
            path = Path(directory) / "window.jpg"
            await self.local_model.command(
                "/usr/sbin/screencapture", "-x", "-o", "-t", "jpg", "-l", str(window), str(path))
            captured_at = time.monotonic()
            return await asyncio.to_thread(self._inspect, path.read_bytes(), captured_at)

    def _inspect(self, data: bytes, captured_at: float) -> GameCapture:
        try:
            quest = read_quest(data)
        except (OSError, RuntimeError, ValueError):
            if not self.quest_error_reported:
                logger.warning("Could not read the 《鸣潮》quest tracker", exc_info=True)
                self.quest_error_reported = True
            quest = None
        return GameCapture(self._thumbnail(data), quest, captured_at)

    @staticmethod
    def _thumbnail(data: bytes) -> bytes:
        with Image.open(BytesIO(data)) as image, BytesIO() as output:
            image.thumbnail((TILE_WIDTH, TILE_HEIGHT))
            image.convert("RGB").save(output, "JPEG", quality=88)
            return output.getvalue()

    async def review(self, frames: tuple[GameFrame, ...]) -> HighlightReview:
        sheet = await asyncio.to_thread(game_contact_sheet, frames)
        raw = await self.local_model.analyze_image(sheet, REVIEW_PROMPT)
        return HighlightReview.parse(raw, frames)

    async def describe(self, frame: GameFrame) -> GameObservation | None:
        text = clean_summary(await self.local_model.analyze_image(frame.image, SCENE_PROMPT))
        if not text or len(text) > 80 or text.strip("。！!「」") == "看不清":
            return None
        return GameObservation(text, frame.captured_at, frame.captured_at)

    async def close(self) -> None:
        await self.local_model.stop()
