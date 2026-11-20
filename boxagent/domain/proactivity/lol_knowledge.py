"""Read-only static League of Legends knowledge used by foreground chat."""

from dataclasses import dataclass
from typing import Literal, Protocol


LolKnowledgeKind = Literal["champion", "item"]


@dataclass(frozen=True)
class LolKnowledgeEntry:
    name: str
    details: dict[str, str]
    version: str
    locale: str
    source_url: str


class LolKnowledge(Protocol):
    async def lookup(self, kind: LolKnowledgeKind, name: str) -> LolKnowledgeEntry | None: ...
