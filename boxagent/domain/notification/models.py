"""Notification domain records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationItem:
    notification_id: str
    dedupe_key: str
    session_id: str
    interaction_id: str
    task_id: str
    outcome: str
    summary: str
    policy: str
    status: str
    attempts: tuple[dict, ...]
    created_at: float
    updated_at: float

    def payload(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "dedupe_key": self.dedupe_key,
            "session_id": self.session_id,
            "interaction_id": self.interaction_id,
            "task_id": self.task_id,
            "outcome": self.outcome,
            "summary": self.summary,
            "policy": self.policy,
            "status": self.status,
            "attempts": [dict(item) for item in self.attempts],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, value: dict):
        return cls(
            notification_id=str(value["notification_id"]),
            dedupe_key=str(value["dedupe_key"]),
            session_id=str(value["session_id"]),
            interaction_id=str(value["interaction_id"]),
            task_id=str(value["task_id"]),
            outcome=str(value["outcome"]),
            summary=str(value["summary"]),
            policy=str(value.get("policy") or "always_notify"),
            status=str(value.get("status") or "pending"),
            attempts=tuple(dict(item) for item in value.get("attempts") or ()),
            created_at=float(value["created_at"]),
            updated_at=float(value["updated_at"]),
        )
