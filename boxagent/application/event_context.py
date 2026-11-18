"""Prepare external runs using the same persona, history and memory projector."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from boxagent.agent.harness.request import ExternalRunInput
from boxagent.agent.harness.request_builder import RuntimeRequestBuilder
from boxagent.agent.runtime.models import RuntimeRequest
from boxagent.domain.conversation.service import ConversationService
from boxagent.domain.environment import EnvironmentContext
from boxagent.domain.events.models import EventBatch


class EventMemory(Protocol):
    async def evidence(self, query: str, *, session_id: str = "", top_k: int = 5) -> Iterable[object]: ...


class SourceContext(Protocol):
    async def __call__(self, batch: EventBatch) -> str: ...


class ExternalEventContext:
    def __init__(self, conversation: ConversationService, builder: RuntimeRequestBuilder,
                 memory: EventMemory, environment: Callable[[], EnvironmentContext], *,
                 sources: Mapping[str, SourceContext] | None = None) -> None:
        self.conversation = conversation
        self.builder = builder
        self.memory = memory
        self.environment = environment
        self.sources = sources or {}

    async def prepare(self, batch: EventBatch) -> tuple[str, RuntimeRequest]:
        session = await self.conversation.active_session()
        session_id = batch.session_id or (session.session_id if session else "")
        if not session_id:
            raise RuntimeError("External event run requires a Product Session")
        turns = await self.conversation.recent_messages(session_id)
        query = "\n".join(event.content[:1000] for event in batch.events)
        memories = await self.memory.evidence(query, session_id=session_id)
        enrich = self.sources.get(batch.events[0].source)
        context = await enrich(batch) if enrich else ""
        return session_id, self.builder.prepare_event(ExternalRunInput(
            run_id=batch.run_id, events=batch.events, turns=turns, memories=memories,
            environment=self.environment(), source_context=context))
