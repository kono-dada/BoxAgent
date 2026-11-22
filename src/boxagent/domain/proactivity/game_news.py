"""Game identity and verified news metadata."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    app_id: int
    name: str


@dataclass(frozen=True)
class GameNews:
    title: str
    source: str
    url: str
    published_at: int
    community_topic: bool = False
