"""Short isolated structured runs over the existing App Server transport."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Protocol

from boxagent.agent.runtime.models import RuntimeRequest
from boxagent.domain.events.models import EventDecision
from boxagent.infrastructure.persistence.sqlite_event_inbox import decision_from_json


class StructuredSession(Protocol):
    async def start(self) -> object: ...
    async def run_isolated_structured_turn(self, *, model: str, instructions: str,
                                          query: str, output_schema: dict) -> str: ...
    async def close(self) -> None: ...


class CodexEventAgent:
    def __init__(self, session: Callable[[], StructuredSession], *, model: str) -> None:
        self.session = session
        self.model = model

    async def evaluate(self, request: RuntimeRequest) -> EventDecision:
        session = self.session()
        try:
            await session.start()
            result = await session.run_isolated_structured_turn(
                model=self.model, instructions=request.developer_instructions,
                output_schema=request.output_schema,
                query=json.dumps({"host_goal": request.query,
                    "conversation_history": [asdict(message) for message in request.history],
                    "evidence_context": request.evidence_context}, ensure_ascii=False))
            return decision_from_json(result)
        finally:
            await session.close()
