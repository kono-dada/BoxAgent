"""音频设备适配；播放状态来自音频回调，不来自模型生成事件。"""

import base64
import threading

import pyaudio


class AudioIO:
    def __init__(self, playing_changed, *, microphone=True, playback=True):
        self.audio = pyaudio.PyAudio()
        self.lock = threading.Lock()
        self.device_lock = threading.Lock()
        self.buffer = bytearray()
        self.playing = False
        self.playing_changed = playing_changed
        self.mic = self.speaker = None
        self.playback = playback
        try:
            if microphone:
                self.mic = self.audio.open(format=pyaudio.paInt16, channels=1, rate=16000,
                                           input=True, frames_per_buffer=1600)
            self.speaker = self.audio.open(format=pyaudio.paInt16, channels=1, rate=24000,
                output=True, frames_per_buffer=1200, stream_callback=self.callback)
        except Exception:
            self.close()
            raise

    def callback(self, _input, frame_count, _time, _status):
        count = frame_count * 2
        with self.lock:
            chunk = bytes(self.buffer[:count])
            del self.buffer[:count]
        playing = bool(chunk)
        if playing != self.playing:
            self.playing = playing
            self.playing_changed(playing)
        output = chunk if self.playback else b""
        return output.ljust(count, b"\0"), pyaudio.paContinue

    def append(self, encoded):
        chunk = base64.b64decode(encoded)
        with self.lock:
            self.buffer.extend(chunk)

    def clear(self):
        with self.lock:
            self.buffer.clear()

    def read(self):
        with self.device_lock:
            if not self.mic:
                raise RuntimeError("麦克风已关闭")
            return self.mic.read(1600, exception_on_overflow=False)

    def close(self):
        with self.device_lock:
            for stream in (self.mic, self.speaker):
                if stream:
                    stream.stop_stream()
                    stream.close()
            self.mic = self.speaker = None
            self.audio.terminate()
