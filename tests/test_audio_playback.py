"""Playback EOF must drain queued PCM and then release the speaking state."""

import threading

from boxagent.infrastructure.audio.pyaudio import AudioIO


def test_provider_eof_drains_pcm_before_releasing_speaking() -> None:
    changes: list[bool] = []
    audio = AudioIO.__new__(AudioIO)
    audio.lock = threading.Lock()
    audio.buffer = bytearray()
    audio.playing = False
    audio.playback_finished = True
    audio.explicit_playback_lifecycle = False
    audio.playback = True
    audio.playing_changed = changes.append
    audio.append_pcm(b'\x01\x00' * 3)
    audio.callback(None, 1, None, None)
    assert changes == [True]
    audio.finish()
    assert changes == [True]
    audio.callback(None, 2, None, None)
    assert changes == [True]
    audio.callback(None, 1, None, None)
    assert changes == [True, False]
    assert not audio.playing
    audio.append_pcm(b'\x01\x00')
    audio.callback(None, 1, None, None)
    assert changes == [True, False, True]

