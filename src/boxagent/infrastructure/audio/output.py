"""Explicit-device PCM output; never changes the system's default device."""

import threading
from typing import Any, Callable, Mapping, Protocol

import pyaudio


class PcmOutput(Protocol):
    sample_rate: int

    def append_pcm(self, pcm: bytes) -> None: ...
    def clear(self) -> None: ...
    def close(self) -> None: ...


def output_device_index(audio: Any, name: str) -> int:
    """Resolve a name at each startup, since PortAudio indexes are not stable."""
    matches = [index for index in range(audio.get_device_count())
               if (device := audio.get_device_info_by_index(index))["name"] == name
               and device["maxOutputChannels"] > 0]
    if len(matches) != 1:
        reason = "未连接" if not matches else "存在重名设备"
        raise RuntimeError(f"桌宠扬声器 {name} {reason}；未切换到其他扬声器")
    return matches[0]


class DeviceOutput:
    """A bounded output-only stream fed by the AOQ playback observer."""

    sample_rate = 48000
    max_buffer_bytes = sample_rate  # 500 ms of mono PCM16, not an unbounded queue.

    def __init__(self, name: str, on_error: Callable[[str], None]) -> None:
        self.name, self.on_error = name, on_error
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.closed = False
        self.failed = False
        self.played_bytes = 0
        self.speaker = None
        self.audio = pyaudio.PyAudio()
        try:
            self.device_index = output_device_index(self.audio, name)
            self.speaker = self.audio.open(
                format=pyaudio.paInt16, channels=1, rate=self.sample_rate,
                input=False, output=True, output_device_index=self.device_index,
                frames_per_buffer=960, stream_callback=self.render)
        except Exception:
            self.close()
            raise

    def append_pcm(self, pcm: bytes) -> None:
        error = ""
        with self.lock:
            if self.closed or self.failed:
                return
            try:
                if self.speaker is None or not self.speaker.is_active():
                    raise RuntimeError("输出设备已断开")
                if len(pcm) % 2:
                    raise ValueError("PCM16 数据长度无效")
                if len(self.buffer) + len(pcm) > self.max_buffer_bytes:
                    raise RuntimeError("播放缓存超时")
                self.buffer.extend(pcm)
            except Exception as exc:
                self.failed = True
                self.buffer.clear()
                error = f"桌宠扬声器 {self.name}：{exc}；未切换到其他扬声器"
        if error:
            self.on_error(error)

    def render(self, _input: bytes | None, frame_count: int,
               _time: Mapping[str, float], _status: int) -> tuple[bytes, int]:
        count = frame_count * 2
        with self.lock:
            if self.closed or self.failed:
                return bytes(count), pyaudio.paComplete
            pcm = bytes(self.buffer[:count])
            del self.buffer[:count]
            self.played_bytes += len(pcm)
        return pcm.ljust(count, b"\0"), pyaudio.paContinue

    def clear(self) -> None:
        with self.lock:
            self.buffer.clear()

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.buffer.clear()
        try:
            if self.speaker is not None:
                try:
                    self.speaker.stop_stream()
                finally:
                    self.speaker.close()
        finally:
            self.speaker = None
            self.audio.terminate()
