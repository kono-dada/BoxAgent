"""Source identities are independent of Product Sessions and Runtime threads."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    source: str
    account_id: str
    event_type: str
    subject_id: str
    occurred_at: float
    observed_at: float
    content: str
    sender_id: str = ""
    subject_name: str = ""
    sender_name: str = ""
    payload_ref: str = ""
    correlation_id: str | None = None


@dataclass(frozen=True)
class EventReceipt:
    event_id: str
    accepted: bool


@dataclass(frozen=True)
class EventDecision:
    action: str
    summary: str
    source_event_ids: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class EventBatch:
    run_id: str
    lease: str
    events: tuple[EventEnvelope, ...]
    session_id: str = ""
    decision: EventDecision | None = None
