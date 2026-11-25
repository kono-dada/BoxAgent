"""Read-only static League of Legends knowledge used by foreground chat."""

from dataclasses import dataclass
from typing import Literal, Protocol

LolKnowledgeKind = Literal["champion", "item", "rune", "summoner", "basic", "champion_detail"]


@dataclass(frozen=True)
class LolKnowledgeEntry:
    name: str
    details: dict[str, str]
    version: str
    locale: str
    source_url: str
    source_name: str = "Riot Data Dragon"


class LolKnowledge(Protocol):
    async def lookup(self, kind: LolKnowledgeKind, name: str) -> LolKnowledgeEntry | None: ...
