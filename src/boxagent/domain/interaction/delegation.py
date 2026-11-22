"""Query-scoped decision advice and delegation acceptance policy."""

import math
import unicodedata
from dataclasses import dataclass
from typing import Literal


def probability(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value) and 0 <= value <= 1:
            return float(value)
    return None


@dataclass(frozen=True)
class DecisionAdvice:
    p_action: float
    p_status: float | None = None
    p_conversation: float | None = None

    @classmethod
    def from_payload(cls, value: object) -> "DecisionAdvice | None":
        if not isinstance(value, dict):
            return None
        action = probability(value.get("p_action"))
        if action is None:
            return None
        return cls(action, probability(value.get("p_status")),
                   probability(value.get("p_conversation")))

    @property
    def exclusion(self) -> Literal["conversation", "status"] | None:
        # Conservative disagreement guard, not an automatic task dispatcher.
        # Ambiguous/contextual confirmations remain Qwen's decision.
        if self.p_action > .2:
            return None
        if self.p_status is not None and self.p_status >= .8:
            return "status"
        if self.p_conversation is not None and self.p_conversation >= .8:
            return "conversation"
        return None


def delegation_exclusion(query: str) -> Literal["conversation"] | None:
    # Punctuation alone never supplies an operation goal. All lexical intent
    # judgments belong to the context-aware decision model, not a word list.
    text = "".join(character for character in query
                   if not character.isspace()
                   and not unicodedata.category(character).startswith("P"))
    return None if text else "conversation"
