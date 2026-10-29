import ctypes
import os
import unittest
from unittest.mock import Mock, patch

import pyaudio

from boxagent.infrastructure.audio.output import DeviceOutput, output_device_index
from boxagent.bootstrap.settings import load_settings
from boxagent.infrastructure.runtimes.qwen.aoq import AoqFramework, AoqRealtimeSession, AoqUnavailable


class OutputSettingsTests(unittest.TestCase):
    def test_explicit_empty_setting_disables_file_override(self) -> None:
        with patch.dict(os.environ, {"BOXAGENT_AOQ_OUTPUT_DEVICE": ""}), patch(
                "boxagent.bootstrap.settings.read_optional_setting",
                side_effect=lambda name: "Display" if name == "BOXAGENT_AOQ_OUTPUT_DEVICE" else ""):
            self.assertEqual(load_settings().aoq_output_device, "")


class AudioOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = Mock()
        self.audio.get_device_count.return_value = 3
        self.audio.get_device_info_by_index.side_effect = lambda index: [
            {"name": "Built-in", "maxOutputChannels": 2},
            {"name": "Display", "maxOutputChannels": 0},
            {"name": "Display", "maxOutputChannels": 2},
        ][index]
        self.stream = self.audio.open.return_value
        self.stream.is_active.return_value = True
        self.patcher = patch("boxagent.infrastructure.audio.output.pyaudio.PyAudio", return_value=self.audio)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.errors: list[str] = []

    def output(self) -> DeviceOutput:
        output = DeviceOutput("Display", self.errors.append)
        self.addCleanup(output.close)
        return output

    def test_only_explicit_output_is_opened_without_a_microphone(self) -> None:
        output = self.output()
        args = self.audio.open.call_args.kwargs
        self.assertEqual(args["output_device_index"], 2)
        self.assertFalse(args["input"])
        self.assertTrue(args["output"])
        self.assertEqual(args["rate"], 48000)
        self.assertEqual(output.sample_rate, 48000)
        self.audio.get_default_output_device_info.assert_not_called()

    def test_missing_or_ambiguous_name_never_falls_back(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "未连接"):
            DeviceOutput("Missing", self.errors.append)
        self.audio.open.assert_not_called()
        self.audio.terminate.assert_called_once()
        self.audio.get_device_info_by_index.side_effect = lambda _: {"name": "Display", "maxOutputChannels": 2}
        with self.assertRaisesRegex(RuntimeError, "重名"):
            output_device_index(self.audio, "Display")

    def test_buffer_is_copied_padded_and_cleared_on_interrupt(self) -> None:
        output = self.output()
        pcm = bytearray(b"\x01\x02\x03\x04")
        output.append_pcm(bytes(pcm))
        pcm[:] = bytes(4)
        rendered, status = output.render(None, 3, {}, 0)
        self.assertEqual(rendered, b"\x01\x02\x03\x04\x00\x00")
        self.assertEqual(status, pyaudio.paContinue)
        self.assertEqual(output.played_bytes, 4)
        output.append_pcm(b"\x01\x02")
        output.clear()
        self.assertEqual(output.render(None, 1, {}, 0)[0], bytes(2))

    def test_slow_output_is_bounded_and_reports_one_error(self) -> None:
        output = self.output()
        output.append_pcm(bytes(output.max_buffer_bytes))
        output.append_pcm(bytes(2))
        output.append_pcm(bytes(2))
        self.assertEqual(len(self.errors), 1)
        self.assertIn("播放缓存超时", self.errors[0])
        self.assertEqual(len(output.buffer), 0)
        self.assertEqual(output.render(None, 1, {}, 0)[1], pyaudio.paComplete)

    def test_disconnected_output_stops_without_reopening_other_devices(self) -> None:
        output = self.output()
        self.stream.is_active.return_value = False
        output.append_pcm(bytes(2))
        self.assertIn("输出设备已断开", self.errors[0])
        self.audio.open.assert_called_once()

    def test_open_failure_and_repeated_close_release_resources(self) -> None:
        self.audio.open.side_effect = OSError("unavailable")
        with self.assertRaisesRegex(OSError, "unavailable"):
            DeviceOutput("Display", self.errors.append)
        self.audio.terminate.assert_called_once()
        self.audio.reset_mock()
        self.audio.open.side_effect = None
        output = self.output()
        output.close()
        output.close()
        output.append_pcm(bytes(2))
        self.assertFalse(output.buffer)
        self.stream.close.assert_called_once()
        self.audio.terminate.assert_called_once()


class AoqOutputTests(unittest.TestCase):
    def framework(self) -> AoqFramework:
        self.events: list[dict] = []
        framework = AoqFramework(sdk_dir="/sdk", work_dir="/work", output_device="Display",
                                 event_callback=self.events.append)
        framework.engine = Mock()
        for method in ("startAudioPlayer_", "startAudioCapture_", "enableAudioFrameObserver_audioSource_config_"):
            getattr(framework.engine, method).return_value = 0
        framework._classes = {name: Mock() for name in (
            "AoqAudioPlaybackConfig", "AoqAudioCaptureConfig", "AoqAudioObserverConfig", "NSData")}
        framework.delegate = Mock()
        self.addCleanup(framework.close)
        return framework

    def test_external_player_preserves_native_microphone_and_read_only_observer(self) -> None:
        framework = self.framework()
        output = Mock(sample_rate=48000)
        with patch("boxagent.infrastructure.audio.output.DeviceOutput", return_value=output) as factory:
            framework._start_audio_devices()
        factory.assert_called_once_with("Display", framework.output_failed)
        player = framework._classes["AoqAudioPlaybackConfig"].alloc.return_value.init.return_value
        capture = framework._classes["AoqAudioCaptureConfig"].alloc.return_value.init.return_value
        observer = framework._classes["AoqAudioObserverConfig"].alloc.return_value.init.return_value
        player.setIsExternal_.assert_called_once_with(True)
        capture.setIsExternal_.assert_called_once_with(False)
        observer.setSampleRate_.assert_called_once_with(48000)
        observer.setMode_.assert_called_once_with(0)
        self.assertEqual(self.events[0], {"type": "_aoq.output_selected", "device": "Display"})

    def test_media_is_copied_only_once_and_silence_is_ignored(self) -> None:
        framework = self.framework()
        framework.output = Mock(sample_rate=48000)
        pcm = bytearray(b"\x01\x02" * 480)
        buffer = (ctypes.c_char * len(pcm)).from_buffer(pcm)
        frame = Mock()
        frame.autoGenMute.return_value = False
        frame.dataSize.return_value = len(pcm)
        frame.numOfSamples.return_value = 480
        frame.samplesPerSec.return_value = 48000
        frame.bytesPerSample.return_value = 2
        frame.numOfChannels.return_value = 1
        frame.dataPtr.return_value = ctypes.addressof(buffer)
        framework.observe_playback_frame(frame)
        pcm[:] = bytes(len(pcm))
        framework.output.append_pcm.assert_called_once_with(b"\x01\x02" * 480)
        framework.append_audio("duplicate data-track delta")
        frame.autoGenMute.return_value = True
        framework.observe_playback_frame(frame)
        self.assertEqual(framework.output.append_pcm.call_count, 1)
        frame.autoGenMute.return_value = False
        frame.samplesPerSec.return_value = 16000
        framework.observe_playback_frame(frame)
        self.assertEqual(self.events[-1]["type"], "_aoq.output_error")

    def test_interrupt_clears_sdk_and_external_audio_close_is_idempotent(self) -> None:
        framework = self.framework()
        output = framework.output = Mock(sample_rate=48000)
        engine = framework.engine
        framework.interrupt()
        engine.interruptAudioPlayer_fadeMs_.assert_called_once_with(0, 20)
        output.clear.assert_called_once()
        framework.close()
        framework.close()
        output.close.assert_called_once()
        engine.setAudioFrameObserver_.assert_called_once_with(None)

    def test_playback_disabled_does_not_open_external_output(self) -> None:
        framework = self.framework()
        with patch("boxagent.infrastructure.audio.output.DeviceOutput") as factory:
            framework._start_audio_devices(playback=False)
        factory.assert_not_called()


class AoqOutputSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_output_failure_is_terminal_and_does_not_request_audio_reconnect(self) -> None:
        session = AoqRealtimeSession(None, lambda *_a, **_kw: None,
                                    sdk_dir="/sdk", work_dir="/work", workspace_id="workspace")
        with self.assertRaisesRegex(AoqUnavailable, "Display disconnected"):
            await session.receive({"type": "_aoq.output_error", "message": "Display disconnected"})
