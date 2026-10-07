"""PyAudio device adapter used by realtime voice providers."""

import base64
import threading

import pyaudio


class AudioIO:
    def __init__(self, playing_changed, *, microphone=True, playback=True,
                 microphone_rate=16000, playback_rate=24000):
        self.audio = pyaudio.PyAudio()
        self.lock = threading.Lock()
        self.device_lock = threading.Lock()
        self.buffer = bytearray()
        self.playing = False
        self.playback_finished = True
        self.explicit_playback_lifecycle = False
        self.playing_changed = playing_changed
        self.mic = self.speaker = None
        self.playback = playback
        try:
            if microphone:
                self.mic = self.audio.open(format=pyaudio.paInt16, channels=1,
                                           rate=microphone_rate,
                                           input=True, frames_per_buffer=1600)
            self.speaker = self.audio.open(format=pyaudio.paInt16, channels=1,
                rate=playback_rate,
                output=True, frames_per_buffer=1200, stream_callback=self.callback)
        except Exception:
            self.close()
            raise

    def callback(self, _input, frame_count, _time, _status):
        count = frame_count * 2
        with self.lock:
            chunk = bytes(self.buffer[:count])
            del self.buffer[:count]
            # Realtime PCM commonly arrives with tiny scheduling gaps. Keep a
            # single logical playback interval open until the provider marks
            # the response audio complete instead of flickering speaking state
            # on every temporary buffer underflow.
            playing = ((bool(chunk) or self.playing)
                       and not self.playback_finished)
            changed = playing != self.playing
            self.playing = playing
        if changed:
            self.playing_changed(playing)
        output = chunk if self.playback else b""
        return output.ljust(count, b"\0"), pyaudio.paContinue

    def append(self, encoded):
        self.append_pcm(base64.b64decode(encoded))

    def append_pcm(self, chunk):
        with self.lock:
            if not self.explicit_playback_lifecycle:
                self.playback_finished = False
            self.buffer.extend(chunk)

    def begin(self):
        """Open an explicitly delimited provider playback response."""
        with self.lock:
            self.explicit_playback_lifecycle = True
            self.playback_finished = False

    def finish(self):
        with self.lock:
            self.playback_finished = True
            changed = self.playing
            self.playing = False
        if changed:
            self.playing_changed(False)

    def clear(self):
        with self.lock:
            self.buffer.clear()
            self.playback_finished = True
            changed = self.playing
            self.playing = False
        if changed:
            self.playing_changed(False)

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
