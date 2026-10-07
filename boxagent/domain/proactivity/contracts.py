"""Reserved contracts; no proactive behavior is enabled in the current product."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProactivityDecision:
    action: str
    reason: str
    confidence: float


class ProactivityPolicy(Protocol):
    async def evaluate(self, context: dict) -> ProactivityDecision: ...
