"""Task evidence and durable state for a game's opening greeting."""

from dataclasses import dataclass

ENTRY_RECEIPT_PREFIX = "game-entry:"


@dataclass(frozen=True)
class TrackedQuest:
    title: str
    objective: str


@dataclass(frozen=True)
class GameCapture:
    image: bytes
    quest: TrackedQuest | None = None
    captured_at: float | None = None


@dataclass(frozen=True)
class QuestProgress:
    launch: str
    quest: TrackedQuest
    observed_at: float


@dataclass(frozen=True)
class EntryHistory:
    current: QuestProgress | None = None
    previous: QuestProgress | None = None
    greeted_launch: str = ""


@dataclass(frozen=True)
class QuestReading:
    text: str
    x: float
    y: float
    height: float
