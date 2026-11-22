"""Keep one native game capture stream and consume its latest complete frame."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from uuid import uuid4
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)
MAX_FRAME_AGE = 1.5
MAX_FRAME_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class CapturedGameFrame:
    image: bytes
    captured_at: float


class GameWindowStream:
    def __init__(self, *, source: Path, cache: Path) -> None:
        self.source = source
        self.cache = cache
        self.process: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task[None] | None = None
        self.errors: asyncio.Task[None] | None = None
        self.latest: CapturedGameFrame | None = None
        self.updated = asyncio.Event()
        self.lock = asyncio.Lock()
        self.window: int | None = None
        self.retry_after = 0.0
        self.binary: Path | None = None

    async def _executable(self) -> Path:
        if self.binary is not None and self.binary.exists():
            return self.binary
        fingerprint = hashlib.sha256(self.source.read_bytes()).hexdigest()[:16]
        self.cache.mkdir(parents=True, exist_ok=True)
        binary = self.cache / f"game-stream-{fingerprint}"
        if not binary.exists():
            temporary = binary.with_suffix("." + uuid4().hex + ".building")
            compiler = await asyncio.create_subprocess_exec(
                "/usr/bin/xcrun", "swiftc", "-parse-as-library", str(self.source),
                "-o", str(temporary), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            try:
                _, errors = await asyncio.wait_for(compiler.communicate(), 60)
                if compiler.returncode:
                    raise RuntimeError("Native game capture build failed: " + errors.decode(errors="replace")[-1500:])
                temporary.replace(binary)
            finally:
                if compiler.returncode is None:
                    compiler.kill()
                    await compiler.wait()
                temporary.unlink(missing_ok=True)
        self.binary = binary
        return binary

    async def frame(self, window: int) -> CapturedGameFrame:
        async with self.lock:
            if (self.window != window or self.process is None or self.process.returncode is not None
                    or self.reader is None or self.reader.done()):
                await self._stop()
                if time.monotonic() < self.retry_after:
                    raise RuntimeError("Game capture is reconnecting")
                binary = await self._executable()
                self.window = window
                self.process = await asyncio.create_subprocess_exec(
                    str(binary), str(window), stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    limit=MAX_FRAME_BYTES)
                self.reader = asyncio.create_task(self._read(self.process), name="game-frame-reader")
                self.errors = asyncio.create_task(self._errors(self.process), name="game-capture-errors")
                logger.info("game.capture.started window=%s pid=%s", window, self.process.pid)
            try:
                async with asyncio.timeout(8):
                    while True:
                        self.updated.clear()
                        frame = self.latest
                        if frame is not None and 0 <= time.monotonic() - frame.captured_at <= MAX_FRAME_AGE:
                            return frame
                        if self.reader.done():
                            raise RuntimeError("Native game capture disconnected")
                        await self.updated.wait()
            except (TimeoutError, RuntimeError):
                self.retry_after = time.monotonic() + 2
                await self._stop()
                raise

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while True:
                header = await process.stdout.readline()
                if not header:
                    break
                value: object = json.loads(header)
                if not isinstance(value, dict):
                    raise ValueError("Invalid game frame header")
                size, captured_at = value.get("size"), value.get("captured_at")
                if (type(size) is not int or not 0 < size <= MAX_FRAME_BYTES
                        or not isinstance(captured_at, (int, float))):
                    raise ValueError("Invalid game frame metadata")
                image = await process.stdout.readexactly(size)
                # Consume continuously so slow model calls never create a frame backlog.
                if 0 <= time.monotonic() - captured_at <= MAX_FRAME_AGE:
                    self.latest = CapturedGameFrame(image, float(captured_at))
                    self.updated.set()
        except (OSError, ValueError, asyncio.IncompleteReadError):
            logger.warning("Native game frame stream ended", exc_info=True)
        finally:
            self.updated.set()

    async def _errors(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while line := await process.stderr.readline():
            logger.warning("game.capture.native %s", line.decode(errors="replace").strip()[:1000])

    async def _stop(self) -> None:
        process, self.process = self.process, None
        self.latest = None
        self.window = None
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self.reader, self.errors):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (self.reader, self.errors) if task is not None),
                             return_exceptions=True)
        self.reader = self.errors = None
        self.latest = None
        self.updated.clear()

    async def close(self) -> None:
        async with self.lock:
            await self._stop()
