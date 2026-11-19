import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path

from boxagent.infrastructure.perception.game_stream import GameWindowStream


class GameStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.worker = self.root / "capture"
        self.worker.write_text(
            f"#!{sys.executable}\n"
            "import json,sys,time\n"
            "for index in range(300):\n"
            " image=f'{sys.argv[1]}:{index}'.encode()\n"
            " header=json.dumps({'size':len(image),'captured_at':time.monotonic()})+'\\n'\n"
            " sys.stdout.buffer.write(header.encode()+image)\n"
            " sys.stdout.buffer.flush()\n"
            " time.sleep(.01)\n")
        self.worker.chmod(0o700)
        self.stream = GameWindowStream(source=self.worker, cache=self.root)
        self.stream.binary = self.worker
        self.addAsyncCleanup(self.stream.close)

    async def asyncTearDown(self) -> None:
        await self.stream.close()
        self.temporary.cleanup()

    async def test_model_delay_does_not_queue_frames_or_restart_worker(self) -> None:
        first = await self.stream.frame(42)
        process = self.stream.process
        await asyncio.sleep(.12)
        latest = await self.stream.frame(42)
        self.assertIs(self.stream.process, process)
        self.assertGreater(latest.captured_at, first.captured_at)
        self.assertLess(time.monotonic() - latest.captured_at, .5)
        self.assertGreater(int(latest.image.split(b":")[1]), int(first.image.split(b":")[1]))

    async def test_window_change_discards_previous_window_and_closes_process(self) -> None:
        await self.stream.frame(42)
        previous = self.stream.process
        current = await self.stream.frame(43)
        self.assertTrue(current.image.startswith(b"43:"))
        self.assertIsNot(self.stream.process, previous)
        assert previous is not None
        self.assertIsNotNone(previous.returncode)
        await self.stream.close()
        self.assertIsNone(self.stream.latest)
        self.assertIsNone(self.stream.process)

    async def test_native_exit_reconnects_without_returning_cached_frame(self) -> None:
        await self.stream.frame(42)
        previous = self.stream.process
        assert previous is not None
        previous.kill()
        await previous.wait()
        current = await self.stream.frame(42)
        self.assertIsNot(self.stream.process, previous)
        self.assertTrue(current.image.startswith(b"42:"))

    async def test_stale_frame_is_discarded(self) -> None:
        self.worker.write_text(
            f"#!{sys.executable}\n"
            "import json,sys,time\n"
            "sys.stdout.buffer.write((json.dumps({'size':3,'captured_at':time.monotonic()-30})+'\\n').encode()+b'old')\n"
            "sys.stdout.buffer.flush()\n")
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            await self.stream.frame(42)
        self.assertIsNone(self.stream.latest)
        self.assertIsNone(self.stream.process)
