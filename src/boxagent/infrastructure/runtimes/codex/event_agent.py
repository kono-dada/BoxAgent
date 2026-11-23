"""Short isolated structured runs over the existing App Server transport."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict

from boxagent.agent.runtime.contracts import StructuredSession
from boxagent.agent.runtime.models import RuntimeRequest
from boxagent.domain.events.models import EventDecision
from boxagent.infrastructure.persistence.sqlite_event_inbox import decision_from_json


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
