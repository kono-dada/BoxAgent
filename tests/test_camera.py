import asyncio
import base64
import json
import unittest
from array import array
from unittest.mock import AsyncMock

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.camera import CameraInteractionService
from boxagent.core.events import StateEvents
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.perception.camera import CameraFrame
from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession
from boxagent.infrastructure.runtimes.qwen.realtime import QwenRealtimeSession
from boxagent.interfaces.engine.server import EngineServer

JPEG = base64.b64encode(b'\xff\xd8\xff\xe0test-jpeg\xff\xd9').decode()


class FakeFeed:
    def __init__(self):
        self.images = []
        self.closed = False

    async def feed_image(self, image):
        self.images.append(image)
        return True


class FakeCameraRuntime:
    def __init__(self):
        self.current = None

    async def open_camera(self):
        self.current = FakeFeed()
        return self.current

    def camera_connected(self, feed):
        return feed is self.current and not feed.closed

    async def close_camera(self, feed):
        feed.closed = True
        if feed is self.current:
            self.current = None


class FrameTests(unittest.TestCase):
    def test_valid_frame_and_no_pixels_in_repr(self):
        frame = CameraFrame.parse(dict(sequence=1, captured_at=100., image=JPEG))
        self.assertEqual(frame.image, JPEG)
        self.assertNotIn(JPEG, repr(frame))

    def test_invalid_frame_metadata_encoding_and_size(self):
        valid = dict(sequence=0, captured_at=100., image=JPEG)
        for field, values in {
            'sequence': [True, -1, 1.5, '1'],
            'captured_at': [float('nan'), float('inf'), True, '1'],
            'image': ['', True, 'not jpeg', 'x' * 256001, 'data:image/jpeg;base64,' + JPEG],
        }.items():
            for value in values:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    CameraFrame.parse({**valid, field: value})


class CameraServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 100.
        self.published = []
        self.events = StateEvents(self.published.append)
        self.runtime = FakeCameraRuntime()
        self.service = CameraInteractionService(self.events, self.runtime,
            clock=lambda: self.now, monotonic=lambda: self.now)
        self.token = (await self.service.start())['token']

    async def asyncTearDown(self):
        await self.service.close()

    def frame(self, sequence, **extra):
        return dict(token=self.token, sequence=sequence, captured_at=self.now, image=JPEG) | extra

    async def test_frames_do_not_create_replies_tasks_or_persist_pixels(self):
        feed = self.runtime.current
        for i in range(4):
            self.now += 1.1
            self.assertTrue((await self.service.send_frame(self.frame(i)))['sent'])
        self.assertEqual(feed.images, [JPEG] * 4)
        self.assertEqual(self.events.state.task, 'idle')
        self.assertEqual(self.events.state.user_text, '')
        self.assertEqual(self.events.state.assistant_text, '')
        self.assertEqual(self.events.state.camera_frames_sent, 4)
        self.assertNotIn(JPEG, str(self.published))
        self.assertNotIn(self.token, str(self.published))

    async def test_rate_limit_drops_frames_without_queueing(self):
        self.assertTrue((await self.service.send_frame(self.frame(0)))['sent'])
        self.now += .2
        self.assertFalse((await self.service.send_frame(self.frame(1)))['sent'])
        self.now += 1
        self.assertTrue((await self.service.send_frame(self.frame(2)))['sent'])
        self.assertEqual(len(self.runtime.current.images), 2)

    async def test_stale_replay_stop_and_old_session(self):
        await self.service.send_frame(self.frame(1))
        for payload in [self.frame(1), self.frame(2, captured_at=0), self.frame(2, token='other')]:
            with self.assertRaises(ValueError): await self.service.send_frame(payload)
        feed = self.runtime.current
        await self.service.stop(self.token)
        self.assertTrue(feed.closed)
        new_token = (await self.service.start())['token']
        self.assertNotEqual(new_token, self.token)
        self.assertFalse((await self.service.stop(self.token))['stopped'])
        with self.assertRaises(ValueError): await self.service.send_frame(self.frame(2))

    async def test_start_revision_excludes_previous_stop_events(self) -> None:
        result = await self.service.start()
        stopped = [event for event in self.published if event['type'] == 'camera.stopped']
        self.assertTrue(all(event['state']['revision'] < result['revision'] for event in stopped))
        self.assertEqual(result['revision'], self.events.state.revision)
        await self.service.stop(result['token'])
        self.assertGreater(self.published[-1]['state']['revision'], result['revision'])

    async def test_slow_send_has_no_backlog_and_cannot_revive_stopped_camera(self):
        entered, complete = asyncio.Event(), asyncio.Event()
        async def delayed(image):
            entered.set()
            await complete.wait()
            return True
        self.runtime.current.feed_image = delayed
        pending = asyncio.create_task(self.service.send_frame(self.frame(1)))
        await entered.wait()
        self.now += 2
        self.assertFalse((await self.service.send_frame(self.frame(2)))['sent'])
        await self.service.stop(self.token)
        complete.set()
        self.assertFalse((await pending)['sent'])
        self.assertEqual(self.events.state.camera_status, 'off')
        self.assertEqual(self.events.state.camera_frames_sent, 0)

    async def test_watchdog_stops_frozen_video(self):
        feed = self.runtime.current
        self.now += 9
        await asyncio.sleep(1.1)
        self.assertEqual(self.events.state.camera_status, 'error')
        self.assertTrue(feed.closed)
        self.assertFalse(self.service.token)

    async def test_waiting_for_speech_is_not_a_camera_timeout(self):
        feed = self.runtime.current
        feed.feed_image = AsyncMock(return_value=False)
        self.now += 20
        self.assertFalse((await self.service.send_frame(self.frame(1)))['sent'])
        await asyncio.sleep(1.1)
        self.assertEqual(self.events.state.camera_status, 'waiting')
        self.assertEqual(self.events.state.camera_frames_sent, 0)
        self.assertFalse(feed.closed)

    async def test_changed_conversation_does_not_receive_old_frames(self):
        old = self.runtime.current
        new = await self.runtime.open_camera()
        with self.assertRaises(ConnectionError): await self.service.send_frame(self.frame(1))
        self.assertTrue(old.closed)
        self.assertFalse(new.closed)
        self.assertEqual(new.images, [])

    async def test_delivery_error_closes_camera(self):
        feed = self.runtime.current
        feed.feed_image = AsyncMock(side_effect=ConnectionError('unavailable'))
        with self.assertRaises(ConnectionError): await self.service.send_frame(self.frame(1))
        self.assertTrue(feed.closed)
        self.assertEqual(self.events.state.camera_status, 'error')

    async def test_engine_routes_and_application_cleanup(self):
        app = BoxAgentApplication(lambda event: None, None, None)
        app.camera = self.service
        server = EngineServer('/unused.sock', None)
        server.application = app
        self.token = (await server.dispatch('camera_start', {}))['token']
        await server.dispatch('camera_frame', self.frame(0))
        self.assertTrue((await server.dispatch('camera_stop', {'token': self.token}))['stopped'])
        await app.close()
        self.assertIsNone(self.service.watchdog)


class QwenCameraTests(unittest.IsolatedAsyncioTestCase):
    async def test_microphone_level_reports_real_pcm_not_connection_status(self):
        emitted = []
        voice = QwenRealtimeSession(None, lambda kind, **data: emitted.append((kind, data)))
        voice.ws = AsyncMock()
        await voice.feed_audio(bytes(320))
        self.assertEqual(emitted[-1], ('voice.input_level', {'voice_input_level': 0.0}))
        voice.last_input_level_at = float('-inf')
        await voice.feed_audio(array('h', [2000] * 160).tobytes())
        self.assertGreater(emitted[-1][1]['voice_input_level'], 0.5)
        self.assertNotIn('audio', emitted[-1][1])

    async def test_websocket_images_require_audio_and_do_not_trigger_a_response(self):
        traces = []
        voice = QwenRealtimeSession(None, lambda *_a, **_k: None,
            trace=lambda kind, **data: traces.append((kind, data)))
        voice.ws = AsyncMock()
        voice.ready.set()
        self.assertFalse(await voice.feed_image(JPEG))
        await voice.feed_audio(b'\x00' * 320)
        self.assertTrue(await voice.feed_image(JPEG))
        messages = [json.loads(call.args[0]) for call in voice.ws.send.call_args_list]
        self.assertEqual([m['type'] for m in messages], ['input_audio_buffer.append', 'input_image_buffer.append'])
        self.assertEqual(messages[-1]['image'], JPEG)
        await voice.receive({'type': 'conversation.item.created', 'item': {
            'content': [{'type': 'input_image', 'image': JPEG}, {'type': 'input_text', 'text': 'hello'}]}})
        self.assertNotIn(JPEG, str(traces))
        self.assertIn('hello', str(traces))

    async def test_aoq_uses_data_track_without_logging_images(self):
        traces, sent = [], []
        voice = AoqRealtimeSession(None, lambda *_a, **_k: None,
            workspace_id='test', sdk_dir='/unused', work_dir='/unused',
            trace=lambda kind, **data: traces.append((kind, data)))
        class Transport:
            def send(self, message): sent.append(message)
        voice.transport = Transport()
        voice.ready.set()
        await voice.receive({'type': 'input_audio_buffer.speech_started'})
        self.assertTrue(await voice.feed_image(JPEG))
        self.assertEqual(sent, [{'type': 'input_image_buffer.append', 'image': JPEG}])
        self.assertNotIn(JPEG, str(traces))

    async def test_aoq_images_wait_for_audio_in_each_turn_without_changing_audio(self):
        sent = []
        voice = AoqRealtimeSession(None, lambda *_a, **_k: None,
            workspace_id='test', sdk_dir='/unused', work_dir='/unused')
        class Transport:
            def send(self, message): sent.append(message)
        voice.transport = Transport()
        voice.ready.set()
        self.assertFalse(await voice.feed_image(JPEG))
        await voice.feed_audio(b'\x00' * 320)
        self.assertEqual(sent, [], 'AOQ must never forward external audio through Data')
        for _ in range(2):
            await voice.receive({'type': 'input_audio_buffer.speech_started'})
            self.assertTrue(await voice.feed_image(JPEG))
            await voice.receive({'type': 'input_audio_buffer.speech_stopped'})
            self.assertFalse(await voice.feed_image(JPEG))
            await voice.receive({'type': 'input_audio_buffer.committed'})
            self.assertFalse(await voice.feed_image(JPEG))
        self.assertEqual([m['type'] for m in sent], ['input_image_buffer.append'] * 2)

    async def test_camera_detach_or_frame_error_preserves_borrowed_voice(self):
        class Voice(FakeFeed):
            def __init__(self):
                super().__init__()
                self.ready, self.done = asyncio.Event(), asyncio.Event()
            async def run(self):
                self.ready.set()
                await self.done.wait()
            async def stop(self):
                self.closed = True
                self.done.set()
        events = StateEvents(lambda event: None)
        service = InteractionService(events, lambda *_a, **_k: Voice(), None)
        camera = CameraInteractionService(events, service, clock=lambda: 100.)
        try:
            await service.toggle()
            original = service.voice
            original_task = service.task
            await camera.start()
            self.assertIs(await service.open_camera(), original)
            await camera.stop()
            self.assertIs(service.voice, original)
            self.assertIs(service.task, original_task)
            self.assertTrue(service.voice_requested)
            self.assertFalse(original.closed)
            token = (await camera.start())['token']
            original.feed_image = AsyncMock(side_effect=ConnectionError('image failed'))
            with self.assertRaises(ConnectionError):
                await camera.send_frame(dict(token=token, sequence=0, captured_at=100., image=JPEG))
            self.assertFalse(original.closed)
            self.assertTrue(service.is_connected)
            self.assertIsNone(service.camera_feed)
        finally:
            await camera.close()
            await service.close()

    async def test_unsupported_camera_does_not_stop_existing_audio_runtime(self):
        class AudioOnly:
            def __init__(self):
                self.ready, self.done = asyncio.Event(), asyncio.Event()
            async def run(self):
                self.ready.set()
                await self.done.wait()
            async def stop(self): self.done.set()
        service = InteractionService(StateEvents(lambda event: None), lambda *_a, **_k: AudioOnly(), None)
        try:
            await service.toggle()
            original = service.voice
            with self.assertRaisesRegex(RuntimeError, '不支持摄像头'):
                await service.open_camera()
            self.assertIs(service.voice, original)
            self.assertTrue(service.is_connected)
        finally:
            await service.close()

    async def test_borrowed_voice_keeps_aoq_reconnect_without_reopening_camera(self):
        created = []
        class Voice(FakeFeed):
            def __init__(self):
                super().__init__()
                self.ready, self.fail, self.done = asyncio.Event(), asyncio.Event(), asyncio.Event()
                created.append(self)
            async def run(self):
                self.ready.set()
                if len(created) == 1:
                    await self.fail.wait()
                    raise RuntimeError('AOQ 语音连接已断开')
                await self.done.wait()
            async def stop(self): self.done.set()
        events = StateEvents(lambda event: None)
        service = InteractionService(events, lambda *_a, **_k: Voice(), None, reconnect_delays=(0,))
        camera = CameraInteractionService(events, service)
        try:
            await service.toggle()
            await camera.start()
            original = service.voice
            original.fail.set()
            for _ in range(100):
                if len(created) == 2 and created[1].ready.is_set(): break
                await asyncio.sleep(.01)
            self.assertEqual(len(created), 2)
            self.assertIsNot(service.voice, original)
            await camera.stop()
            self.assertIs(service.voice, created[1])
            self.assertTrue(service.is_connected)
            self.assertIsNone(service.camera_feed)
            self.assertFalse(camera.token)
        finally:
            await camera.close()
            await service.close()

    async def test_closed_or_audio_only_runtime_rejected(self):
        voice = QwenRealtimeSession(None, None, model='qwen-audio-3.0-realtime-plus')
        with self.assertRaises(ValueError): await voice.feed_image(JPEG)
        voice.model = 'qwen3.8-omni-flash-realtime'
        voice.closed = True
        with self.assertRaises(ConnectionError): await voice.feed_image(JPEG)

    async def test_camera_reuses_voice_and_does_not_close_new_session(self):
        class Voice(FakeFeed):
            def __init__(self):
                super().__init__()
                self.ready, self.done = asyncio.Event(), asyncio.Event()
            async def run(self):
                self.ready.set()
                await self.done.wait()
            async def stop(self):
                self.closed = True
                self.done.set()
        service = InteractionService(StateEvents(lambda event: None), lambda *_a, **_k: Voice(), None)
        try:
            await service.toggle()
            old = service.voice
            self.assertIs(await service.open_camera(), old)
            await service.reset_session()
            new = await service.open_camera()
            await service.close_camera(old)
            self.assertIs(service.voice, new)
            self.assertTrue(service.camera_connected(new))
            await service.close_camera(new)
            self.assertTrue(new.closed)
        finally:
            await service.close()
