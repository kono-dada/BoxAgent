"""Input owned by the Harness before it is compiled for a Runtime."""

from dataclasses import dataclass
from typing import Iterable

from boxagent.domain.environment import EnvironmentContext
from boxagent.domain.events.models import EventEnvelope


@dataclass(frozen=True)
class HarnessInput:
    goal: str
    turns: Iterable[object] = ()
    memories: Iterable[object] = ()
    environment: EnvironmentContext | None = None
    context_cursor: int = 0
    synced_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExternalRunInput:
    run_id: str
    events: tuple[EventEnvelope, ...]
    turns: Iterable[object] = ()
    memories: Iterable[object] = ()
    environment: EnvironmentContext | None = None
    source_context: str = ""
