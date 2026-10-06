"""Voice session port."""

from typing import Protocol


class VoiceSession(Protocol):
    async def run(self) -> None: ...
    async def stop(self) -> None: ...
