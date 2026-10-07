"""Persistence ports required by the notification domain."""

from typing import Protocol

from boxagent.domain.notification.models import NotificationItem


class NotificationRepository(Protocol):
    def start(self) -> None: ...
    def list(self) -> list[NotificationItem]: ...
    def save(self, item: NotificationItem) -> NotificationItem: ...
