import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from boxagent.bootstrap.engine import create_voice_factory
from boxagent.infrastructure.runtimes.qwen.aoq import (
    AoqFramework,
    AoqRealtimeSession,
    AoqToken,
    AoqTokenClient,
    AoqUnavailable,
    aoq_preflight,
)
from boxagent.infrastructure.runtimes.qwen.realtime import MODELS, QwenRealtimeSession


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class FakeNative:
    def __init__(self):
        self.sent = []
        self.interrupted = False
        self.idle = False
        self.closed = False
        self.began = False
        self.audio = []
        self.media_enabled = []

    def send(self, message):
        self.sent.append(message)

    def start(self, _token, *, microphone=True, playback=True) -> None:
        pass

    def interrupt(self):
        self.interrupted = True

    def mark_playback_idle(self):
        self.idle = True

    def begin_playback(self):
        self.began = True

    def append_audio(self, data):
        self.audio.append(data)

    def enable_audio_send(self, enabled):
        self.media_enabled.append(enabled)

    def close(self):
        self.closed = True


class AoqTokenTests(unittest.TestCase):
    def test_token_client_maps_official_response_and_request(self):
        captured = {}
        payload = {
            "sid": "sid-1",
            "aoqTokenForClient": "token-1",
            "clientRelayEndpoints": [
                {"endpoint": "relay.example", "port": 8443, "route_index": 4},
            ],
            "clientRelayCertFingerprint": "sha256/example",
            "sidExpiresInSecs": 7200,
            "extraInfo": {"workspaceIdHash": "workspace-hash"},
        }

        def open_request(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(payload)

        token = AoqTokenClient(
            api_key="secret", workspace_id="workspace", region="cn-beijing",
            opener=open_request).allocate("qwen-audio-3.0-realtime-plus")

        self.assertEqual(token, AoqToken.from_payload(payload))
        self.assertEqual(captured["timeout"], 15)
        self.assertIn(
            "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime",
            captured["request"].full_url)
        self.assertIn("model=qwen-audio-3.0-realtime-plus",
                      captured["request"].full_url)
        self.assertEqual(
            captured["request"].get_header("X-dashscope-rtc-transport"), "moq")
        self.assertEqual(captured["request"].data, b"{}")

    def test_incomplete_token_response_is_rejected(self):
        with self.assertRaisesRegex(AoqUnavailable, "clientRelayEndpoints"):
            AoqToken.from_payload({
                "sid": "sid", "aoqTokenForClient": "token",
                "clientRelayCertFingerprint": "sha256/value",
                "extraInfo": {"workspaceIdHash": "hash"},
            })

    def test_preflight_explains_workspace_and_sdk_fallbacks(self):
        available, reason = aoq_preflight(
            sdk_dir="/missing", workspace_id="", platform="darwin")
        self.assertFalse(available)
        self.assertIn("WORKSPACE_ID", reason)
        with tempfile.TemporaryDirectory() as temporary:
            available, reason = aoq_preflight(
                sdk_dir=temporary, workspace_id="workspace", platform="darwin")
        self.assertFalse(available)
        self.assertIn("AoqClientSdk.framework", reason)


class AoqSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_statistics_do_not_extend_handshake_deadline(self) -> None:
        native = FakeNative()
        session = AoqRealtimeSession(
            lambda *_: None, lambda *_a, **_k: None,
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work",
            connect_timeout=.03, native_factory=lambda **_: native,
            token_client_factory=lambda **_: SimpleNamespace(allocate=lambda _: None))

        async def statistics() -> None:
            while True:
                session.events.put_nowait({"type": "_aoq.audio_stats", "bytes": 0})
                await asyncio.sleep(.002)

        producer = asyncio.create_task(statistics())
        try:
            with self.assertRaisesRegex(AoqUnavailable, "AOQ 服务器连接超时"):
                await asyncio.wait_for(session.run(), .5)
            self.assertTrue(native.closed)
            self.assertFalse(session.ready.is_set())
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def test_stop_during_handshake_exits_without_waiting_for_network(self) -> None:
        native = FakeNative()
        session = AoqRealtimeSession(
            lambda *_: None, lambda *_a, **_k: None,
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work",
            native_factory=lambda **_: native,
            token_client_factory=lambda **_: SimpleNamespace(allocate=lambda _: None))
        task = asyncio.create_task(session.run())
        try:
            async with asyncio.timeout(1):
                while session.transport is None:
                    await asyncio.sleep(.001)
            await session.stop()
            await asyncio.wait_for(task, .2)
            self.assertTrue(native.closed)
            self.assertFalse(session.ready.is_set())
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def test_camera_support_preserves_native_audio_tracks_and_codecs(self):
        framework = AoqFramework(
            sdk_dir="/sdk", work_dir="/work", event_callback=lambda _: None)
        framework.engine = Mock()
        framework.engine.setAudioEncoderConfig_.return_value = 0
        framework.engine.setAudioDecoderConfig_.return_value = 0
        audio_track, data_track = Mock(), Mock()
        encoder, decoder = Mock(), Mock()
        connect = Mock()
        framework._classes = {
            "AoqConnectConfig": Mock(),
            "AoqTrackParam": Mock(),
            "AoqAudioCodecConfig": Mock(),
        }
        framework._classes["AoqConnectConfig"].alloc.return_value.init.return_value = connect
        framework._classes["AoqTrackParam"].alloc.return_value.init.side_effect = [audio_track, data_track]
        framework._classes["AoqAudioCodecConfig"].alloc.return_value.init.side_effect = [encoder, decoder]
        token = AoqToken("token", "sid", "fingerprint", (), "workspace-hash")

        self.assertIs(framework._connect_config(token), connect)
        framework._configure_audio_codecs()

        audio_track.setTrackType_.assert_called_once_with(0)
        data_track.setTrackType_.assert_called_once_with(2)
        connect.setPublishTracks_.assert_called_once_with([audio_track, data_track])
        connect.setSubscribeTracks_.assert_called_once_with([audio_track, data_track])
        audio_track.setTrackMode_.assert_not_called()
        for config in (encoder, decoder):
            config.setTrackType_.assert_called_once_with(0)
            config.setCodecType_.assert_called_once_with(2)
            config.setSampleRate_.assert_called_once_with(16000)
            config.setChannel_.assert_called_once_with(1)
            config.setBitrate_.assert_called_once_with(32000)

    def test_camera_support_keeps_native_capture_playback_and_read_only_observer(self):
        framework = AoqFramework(
            sdk_dir="/sdk", work_dir="/work", event_callback=lambda _: None)
        framework.engine = Mock()
        framework.delegate = Mock()
        for method in ("startAudioCapture_", "startAudioPlayer_",
                       "enableAudioFrameObserver_audioSource_config_"):
            getattr(framework.engine, method).return_value = 0
        framework._classes = {name: Mock() for name in (
            "AoqAudioCaptureConfig", "AoqAudioPlaybackConfig", "AoqAudioObserverConfig")}

        framework._start_audio_devices()

        capture = framework._classes["AoqAudioCaptureConfig"].alloc.return_value.init.return_value
        player = framework._classes["AoqAudioPlaybackConfig"].alloc.return_value.init.return_value
        observer = framework._classes["AoqAudioObserverConfig"].alloc.return_value.init.return_value
        capture.setIsExternal_.assert_called_once_with(False)
        player.setIsExternal_.assert_called_once_with(False)
        framework.engine.startAudioCapture_.assert_called_once_with(capture)
        framework.engine.startAudioPlayer_.assert_called_once_with(player)
        observer.setMode_.assert_called_once_with(0)
        framework.engine.enableAudioFrameObserver_audioSource_config_.assert_called_once_with(
            True, 3, observer)
        framework.engine.startVideoCapture_.assert_not_called()
        framework.engine.muteAudioCapture_.assert_not_called()

    def test_qwen_38_waits_through_a_natural_speaking_pause(self):
        turn_detection = MODELS[
            "qwen3.8-omni-flash-realtime"]["turn_detection"]

        self.assertEqual(turn_detection["type"], "semantic_vad")
        self.assertEqual(turn_detection["silence_duration_ms"], 1500)

    def test_generated_silence_does_not_mark_playback_started(self):
        states = []
        framework = AoqFramework(sdk_dir="/sdk", work_dir="/work",
                                 event_callback=lambda _: None, playback_callback=states.append)

        class Frame:
            def autoGenMute(self):
                return True

        framework.observe_playback_frame(Frame())

        self.assertEqual(states, [])
        self.assertFalse(framework._playback_observed)

    def test_real_frame_marks_playback_started_once(self):
        states = []
        framework = AoqFramework(sdk_dir="/sdk", work_dir="/work",
                                 event_callback=lambda _: None, playback_callback=states.append)

        class Frame:
            def autoGenMute(self):
                return False

        framework.observe_playback_frame(Frame())
        framework.observe_playback_frame(Frame())

        self.assertEqual(states, [True])

    def test_native_playback_observer_does_not_replace_data_track_audio(self):
        chunks = []
        framework = AoqFramework(sdk_dir="/sdk", work_dir="/work", event_callback=lambda _: None)
        framework.audio_output = SimpleNamespace(append_pcm=chunks.append)

        class Pointer:
            def as_buffer(self, size):
                return memoryview(b"\x01\x02\x03\x04"[:size])

        class Frame:
            def autoGenMute(self):
                return False

            def dataSize(self):
                return 4

            def dataPtr(self):
                return Pointer()

        framework.observe_playback_frame(Frame())

        self.assertEqual(chunks, [])

    async def test_data_track_uses_existing_qwen_protocol_and_native_barge_in(self):
        session = AoqRealtimeSession(
            lambda *_args: None, lambda *_args, **_kwargs: None,
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work")
        native = FakeNative()
        session.transport = native
        session.audio = SimpleNamespace(clear=native.interrupt)
        session.ws = session
        session.ready.set()
        session.response_active = True
        session.response_id = "response-1"

        await session.submit_text("你好")
        await session.receive({"type": "input_audio_buffer.speech_started"})

        self.assertEqual(native.sent[0]["type"], "response.cancel")
        self.assertEqual(native.sent[1]["item"]["role"], "user")
        self.assertEqual(native.sent[2]["type"], "response.create")
        self.assertTrue(native.interrupted)

    def test_data_track_audio_is_not_replayed_by_external_player(self):
        framework = object.__new__(AoqFramework)

        self.assertIsNone(framework.append_audio("base64-is-not-used"))

    def test_native_playback_finishes_when_response_audio_ends(self):
        states = []
        framework = object.__new__(AoqFramework)
        framework._playback_observed = True
        framework.playback_callback = states.append

        framework.mark_playback_idle()

        self.assertEqual(states, [False])

    async def test_session_updated_opens_media_after_handshake(self):
        session = AoqRealtimeSession(
            lambda *_args: None, lambda *_args, **_kwargs: None,
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work")
        native = FakeNative()
        session.transport = native
        session.audio = SimpleNamespace(append=native.append_audio)
        session.inject_history = unittest.mock.AsyncMock()

        await session.receive({"type": "session.updated"})

        self.assertEqual(native.media_enabled, [True])
        session.inject_history.assert_awaited_once_with()
        self.assertTrue(session.ready.is_set())
        self.assertFalse(session.audio_input_ready.is_set(),
                         'handshake alone is not evidence that audio reached the model')

    async def test_native_stats_are_metadata_not_proof_of_speech(self):
        traces = []
        session = AoqRealtimeSession(
            lambda *_args: None, lambda *_args, **_kwargs: None,
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work",
            trace=lambda kind, **payload: traces.append((kind, payload)))
        await session.receive({"type": "_aoq.audio_stats", "bytes": 1280,
                               "bitrate": 32000, "encode_volume": 0})
        self.assertEqual(traces[-1][0], "transport.audio_publish")
        self.assertEqual(traces[-1][1]["bytes"], 1280)
        self.assertFalse(session.audio_input_ready.is_set())
        self.assertFalse(session.user_speaking)

    async def test_notification_receipt_survives_media_data_track_race(self):
        receipts = []
        session = AoqRealtimeSession(
            lambda *_args: None, lambda *_args, **_kwargs: None,
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work",
            notification_event=lambda kind, payload: receipts.append(
                (kind, payload)))
        session.pending_notifications.add("notice-1")
        session.response_origins.append({
            "kind": "notification", "notification_id": "notice-1"})
        session.playing = True

        await session.receive({
            "type": "response.created", "response": {"id": "response-1"}})

        self.assertEqual(receipts, [("playback_started", {
            "notification_id": "notice-1",
        })])
        self.assertNotIn("notice-1", session.pending_notifications)

    async def test_runtime_connection_failure_is_delegated_to_lifecycle_owner(self):
        emitted = []
        session = AoqRealtimeSession(
            lambda *_args: None,
            lambda kind, **payload: emitted.append((kind, payload)),
            workspace_id="workspace", sdk_dir="/sdk", work_dir="/work")

        with self.assertRaisesRegex(AoqUnavailable, "语音连接已断开"):
            await session.receive({"type": "_aoq.connection", "status": 3})

        self.assertNotIn("voice.error", [kind for kind, _payload in emitted])


class AoqFactoryTests(unittest.TestCase):
    def _settings(self, root, **overrides):
        values = dict(
            qwen_transport="auto", aoq_sdk_dir=root / "missing",
            qwen_workspace_id="", qwen_region="cn-beijing",
            aoq_work_dir=root / "work", log_dir=root / "logs",
            voice_model="qwen-audio-3.0-realtime-plus", qwen_api_key="key",
        )
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_auto_falls_back_to_websocket_when_aoq_is_not_configured(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factory = create_voice_factory(
                self._settings(root), environment_provider=SimpleNamespace(
                    capture=lambda: None),
                context_projector=SimpleNamespace(environment_packet=lambda _value: ""))
            self.assertIs(factory.func, QwenRealtimeSession)
            record = json.loads((root / "logs/runtime/qwen/events.jsonl").read_text())
        self.assertEqual(record["event"], "transport.fallback")
        self.assertEqual(record["selected"], "websocket")

    def test_auto_selects_aoq_when_frameworks_and_workspace_exist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            (sdk / "AoqClientSdk.framework").mkdir(parents=True)
            (sdk / "PluginOpus.framework").mkdir()
            factory = create_voice_factory(self._settings(
                root, aoq_sdk_dir=sdk, qwen_workspace_id="workspace"))
        self.assertIs(factory.func, AoqRealtimeSession)
        self.assertEqual(factory.keywords["workspace_id"], "workspace")

    def test_websocket_direct_is_explicit_and_scoped_to_qwen(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._settings(Path(temporary), qwen_transport='websocket',
                qwen_websocket_proxy='direct', qwen_workspace_id='workspace',
                voice_model='qwen3.8-omni-flash-realtime')
            factory = create_voice_factory(settings)
        self.assertIsNone(factory.keywords['websocket_proxy'])
        self.assertTrue(factory.keywords['websocket_url'].startswith('wss://workspace.cn-beijing.maas.aliyuncs.com/'))

    def test_explicit_output_device_reaches_aoq_but_cannot_silently_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sdk = root / "sdk"
            (sdk / "AoqClientSdk.framework").mkdir(parents=True)
            (sdk / "PluginOpus.framework").mkdir()
            settings = self._settings(root, aoq_sdk_dir=sdk, qwen_workspace_id="workspace",
                                      aoq_output_device="Display")
            factory = create_voice_factory(settings)
            self.assertEqual(factory.keywords["output_device"], "Display")
            settings.qwen_transport = "websocket"
            with self.assertRaisesRegex(ValueError, "未回退"):
                create_voice_factory(settings)

    def test_websocket_auto_keeps_proxy_discovery_and_rejects_typos(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = self._settings(Path(temporary), qwen_transport='websocket')
            self.assertIs(create_voice_factory(settings).keywords['websocket_proxy'], True)
            settings.qwen_websocket_proxy = 'typo'
            with self.assertRaises(ValueError): create_voice_factory(settings)


if __name__ == "__main__":
    unittest.main()
