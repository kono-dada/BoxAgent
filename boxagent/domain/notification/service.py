"""Persistent completion notifications independent from a concrete delivery channel."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace

from boxagent.core.ids import new_notification_id
from boxagent.domain.notification.contracts import NotificationRepository
from boxagent.domain.notification.models import NotificationItem


class NotificationOutbox:
    """Own idempotency and delivery state; channel adapters only claim and ack."""

    def __init__(self, repository: NotificationRepository | None = None):
        self.repository = repository
        self.lock = asyncio.Lock()

    async def start(self):
        if self.repository is None:
            return
        self.repository.start()
        async with self.lock:
            for item in self.repository.list():
                if item.status == "delivering":
                    self.repository.save(replace(
                        item, status="pending", updated_at=time.time()))

    async def enqueue(self, *, session_id: str, interaction_id: str,
                      task_id: str, outcome: str, summary: str,
                      policy="always_notify") -> NotificationItem | None:
        if self.repository is None or not session_id or not interaction_id or not task_id:
            return None
        dedupe_key = f"{session_id}:{interaction_id}:{task_id}"
        async with self.lock:
            existing = next((item for item in self.repository.list()
                             if item.dedupe_key == dedupe_key), None)
            if existing:
                return existing
            now = time.time()
            return self.repository.save(NotificationItem(
                notification_id=new_notification_id(), dedupe_key=dedupe_key,
                session_id=session_id, interaction_id=interaction_id,
                task_id=task_id, outcome=outcome, summary=summary.strip(),
                policy=policy, status="pending", attempts=(),
                created_at=now, updated_at=now))

    async def pending(self) -> list[NotificationItem]:
        if self.repository is None:
            return []
        async with self.lock:
            return [item for item in self.repository.list()
                    if item.status in {"pending", "delivering"}]

    async def claim(self, notification_id: str, channel: str) -> NotificationItem:
        async with self.lock:
            item = self._get(notification_id)
            if item.status == "delivered":
                return item
            attempt = {"channel": channel, "status": "delivering",
                       "occurred_at": time.time()}
            return self.repository.save(replace(
                item, status="delivering",
                attempts=(*item.attempts, attempt), updated_at=time.time()))

    async def acknowledge(self, notification_id: str, channel: str,
                          receipt="delivered") -> NotificationItem:
        async with self.lock:
            item = self._get(notification_id)
            if item.status == "delivered":
                return item
            attempt = {"channel": channel, "status": "delivered",
                       "receipt": receipt, "occurred_at": time.time()}
            return self.repository.save(replace(
                item, status="delivered",
                attempts=(*item.attempts, attempt), updated_at=time.time()))

    async def retry(self, notification_id: str, channel: str,
                    error="delivery_interrupted") -> NotificationItem:
        async with self.lock:
            item = self._get(notification_id)
            if item.status == "delivered":
                return item
            attempt = {"channel": channel, "status": "failed", "error": error,
                       "occurred_at": time.time()}
            return self.repository.save(replace(
                item, status="pending",
                attempts=(*item.attempts, attempt), updated_at=time.time()))

    def _get(self, notification_id):
        if self.repository is None:
            raise RuntimeError("Notification Outbox 未配置")
        item = next((item for item in self.repository.list()
                     if item.notification_id == notification_id), None)
        if item is None:
            raise FileNotFoundError(f"通知不存在：{notification_id}")
        return item
