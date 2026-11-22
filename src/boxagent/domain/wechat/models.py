"""WeChat integration domain records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from boxagent.domain.events.models import EventEnvelope

CALL_PREVIEW = "[音视频通话]"


@dataclass(frozen=True)
class Contact:
    peer: str
    nickname: str
    remark: str = ""

    @property
    def display_name(self) -> str:
        return self.remark or self.nickname or self.peer

    @property
    def is_group(self) -> bool:
        return self.peer.endswith("@chatroom")


@dataclass(frozen=True)
class Conversation:
    peer: str
    display_name: str
    unread: int
    summary: str
    updated_at: int


@dataclass(frozen=True)
class Message:
    peer: str
    database: str
    local_id: int
    server_id: str
    sender_id: str
    text: str
    local_type: int
    created_at: int
    source: str = ""


@dataclass(frozen=True)
class GroupMember:
    group: str
    peer: str
    display_name: str
    membership_verified: bool = False


@dataclass(frozen=True)
class DeliveryCursor:
    watermarks: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class DraftSnapshot:
    """Current composer draft of the active conversation."""

    units: int

    @property
    def empty(self) -> bool:
        return self.units == 0


@dataclass(frozen=True)
class SendOutcome:
    """Result of one native text send."""

    peer: str
    text: str
    local_id: int | None = None
    server_id: int | None = None
    request_id: str = ""
    submitted: bool = False
    dry_run: bool = False
    error_code: str | None = None

    @property
    def persisted(self) -> bool:
        return self.local_id is not None

    @property
    def acknowledged(self) -> bool:
        """A non-zero server id means the backend accepted the message."""
        return self.persisted and bool(self.server_id)

    @property
    def status(self) -> str:
        if self.error_code == "outcome_unknown":
            return "outcome_unknown"
        if self.dry_run:
            return "dry_run_passed"
        if self.acknowledged:
            return "acknowledged"
        if self.persisted:
            return "persisted"
        return "submitted_unconfirmed" if self.submitted else "rejected"


@dataclass(frozen=True)
class DeliveryCheck:
    """Outcome of matching a sent text against the local message database."""

    local_id: int | None
    server_id: int | None
    matched: bool


@dataclass(frozen=True)
class PendingMessage:
    event: EventEnvelope
    attempts: int


@dataclass(frozen=True)
class Capability:
    state: Literal["ready", "unavailable", "experimental", "not_configured"]
    reason: str = ""


@dataclass(frozen=True)
class WeChatHealth:
    account_id: str
    reading: Capability
    listening: Capability
    sending: Capability
    moments: Capability


@dataclass(frozen=True)
class PublishOutcome:
    request_id: str
    text: str
    visibility: str
    status: str
    delivery_confirmed: bool = False
    detail: str = ""
