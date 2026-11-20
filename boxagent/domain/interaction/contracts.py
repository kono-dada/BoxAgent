"""Voice session port."""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from boxagent.domain.proactivity.game_highlight import GameHighlight


class VoiceSession(Protocol):
    async def run(self) -> None: ...
    async def stop(self) -> None: ...


@runtime_checkable
class GameHighlightVoice(Protocol):
    async def announce_game_highlight(self, highlight: GameHighlight,
                                      still_current: Callable[[], bool]) -> bool: ...


@runtime_checkable
class GameLineVoice(Protocol):
    async def announce_game_line(self, receipt: str, text: str,
                                 still_current: Callable[[], bool]) -> bool: ...


@runtime_checkable
class CompanionSpeechVoice(Protocol):
    async def set_game_speech_muted(self, muted: bool) -> None: ...


@runtime_checkable
class LolSpeechVoice(Protocol):
    def schedule_lol(self, line: str, *, still_current: Callable[[], bool],
                     playback_started: Callable[[], None]) -> bool: ...
