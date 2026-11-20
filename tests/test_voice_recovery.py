"""Persistent user voice intent, bounded retry delay and explicit-stop ownership."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from boxagent.core.events import StateEvents
from boxagent.domain.interaction.service import InteractionService


class VoiceRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_wake_uses_same_persistent_request_as_manual_voice_toggle(self) -> None:
        voices = []

        class Voice:
            def __init__(self) -> None:
                self.first = not voices
                self.ready, self.closed = asyncio.Event(), asyncio.Event()
                voices.append(self)

            async def run(self) -> None:
                if self.first:
                    raise TimeoutError("wake handshake timeout")
                self.ready.set()
                await self.closed.wait()

            async def stop(self) -> None:
                self.closed.set()

        service = InteractionService(StateEvents(lambda _: None),
            lambda *_a, **_k: Voice(), AsyncMock(), reconnect_delays=(0,))
        try:
            with self.assertRaisesRegex(RuntimeError, "wake handshake timeout"):
                await service.wake("你好")
            async with asyncio.timeout(1):
                while len(voices) < 2 or not voices[-1].ready.is_set():
                    await asyncio.sleep(.001)
            self.assertTrue(service.voice_requested)
            self.assertTrue(service.is_connected)
        finally:
            await service.close()

    async def test_timeout_retries_beyond_old_limit_and_only_user_stop_revokes_intent(self) -> None:
        voices = []
        disconnected = asyncio.Event()

        class Voice:
            def __init__(self) -> None:
                self.index = len(voices)
                self.ready, self.closed = asyncio.Event(), asyncio.Event()
                voices.append(self)

            async def run(self) -> None:
                if 1 <= self.index <= 4:
                    raise TimeoutError("provider handshake timed out")
                self.ready.set()
                if self.index == 0:
                    await disconnected.wait()
                    # A response-ack failure closes the provider transport;
                    # some transports finish normally instead of raising.
                    return
                await self.closed.wait()

            async def stop(self) -> None:
                self.closed.set()

        events = []
        service = InteractionService(StateEvents(events.append),
            lambda *_args, **_kwargs: Voice(), AsyncMock(), reconnect_delays=(0, 0, 0))
        real_sleep = asyncio.sleep

        async def fast_sleep(delay: float) -> None:
            await real_sleep(min(delay, .001))

        try:
            with patch("boxagent.domain.interaction.service.asyncio.sleep", fast_sleep):
                await service.toggle()
                disconnected.set()
                async with asyncio.timeout(2):
                    while len(voices) < 6 or not voices[-1].ready.is_set():
                        await real_sleep(.001)
                self.assertTrue(service.voice_requested)
                self.assertTrue(service.is_connected)
                self.assertNotIn("voice.off", [e["type"] for e in events])
                await service.toggle()
                count = len(voices)
                await real_sleep(.02)
                self.assertEqual(len(voices), count)
                self.assertFalse(service.voice_requested)
                self.assertFalse(service.is_connected)
                self.assertIsNone(service.reconnect_task)
        finally:
            await service.close()

    async def test_stop_during_retry_handshake_cleans_late_connection(self) -> None:
        voices = []
        disconnect, retry_started = asyncio.Event(), asyncio.Event()

        class Voice:
            def __init__(self) -> None:
                self.first = not voices
                self.ready, self.closed = asyncio.Event(), asyncio.Event()
                voices.append(self)

            async def run(self) -> None:
                if self.first:
                    self.ready.set()
                    await disconnect.wait()
                    raise TimeoutError("ack timeout")
                retry_started.set()
                await self.closed.wait()
                # A late handshake cannot resurrect the microphone after stop.
                self.ready.set()

            async def stop(self) -> None:
                self.closed.set()

        service = InteractionService(StateEvents(lambda _: None),
            lambda *_a, **_k: Voice(), AsyncMock(), reconnect_delays=(0,))
        try:
            await service.toggle()
            disconnect.set()
            await asyncio.wait_for(retry_started.wait(), 1)
            await asyncio.wait_for(service.toggle(), 1)
            self.assertTrue(voices[-1].closed.is_set())
            self.assertFalse(service.voice_requested)
            self.assertFalse(service.is_connected)
            self.assertEqual(service.events.state.voice, "off")
        finally:
            await service.close()

    async def test_camera_owned_voice_does_not_resume_when_camera_disconnects(self) -> None:
        voices: list[Feed] = []
        disconnect = asyncio.Event()

        class Feed:
            def __init__(self) -> None:
                self.ready = asyncio.Event()
                self.stopped = asyncio.Event()
                voices.append(self)

            async def run(self) -> None:
                self.ready.set()
                await (disconnect if self is voices[0] else self.stopped).wait()

            async def stop(self) -> None:
                self.stopped.set()

            async def feed_image(self, image: str) -> bool:
                return True

        service = InteractionService(StateEvents(lambda _: None),
            lambda *_args, **_kwargs: Feed(), AsyncMock(), reconnect_delays=(.01,))
        try:
            feed = await service.open_camera()
            disconnect.set()
            async with asyncio.timeout(1):
                while service.is_connected:
                    await asyncio.sleep(.001)
            await service.close_camera(feed)
            self.assertFalse(service.voice_requested)
            self.assertIsNone(service.reconnect_task)
            self.assertEqual(len(voices), 1)
        finally:
            await service.close()
