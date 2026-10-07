"""Input owned by the Harness before it is compiled for a Runtime."""

from dataclasses import dataclass
from typing import Iterable

from boxagent.domain.environment import EnvironmentContext


@dataclass(frozen=True)
class HarnessInput:
    goal: str
    turns: Iterable[object] = ()
    memories: Iterable[object] = ()
    environment: EnvironmentContext | None = None
    context_cursor: int = 0
