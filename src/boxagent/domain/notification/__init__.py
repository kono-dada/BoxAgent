"""Persistent user notification domain."""

from boxagent.domain.notification.models import NotificationItem
from boxagent.domain.notification.service import NotificationOutbox

__all__ = ["NotificationItem", "NotificationOutbox"]
