"""Atomic JSON materialization of the completion notification outbox."""

import json
import os
import threading
from pathlib import Path
from typing import Any, TypedDict, cast

from boxagent.domain.notification import NotificationItem

SCHEMA_VERSION = 1


class _OutboxPayload(TypedDict):
    schema_version: int
    items: list[dict[str, Any]]


class JsonNotificationRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.RLock()

    def start(self) -> None:
        with self.lock:
            if not self.path.exists():
                self._write({"schema_version": SCHEMA_VERSION, "items": []})
            else:
                self._read()

    def list(self) -> list[NotificationItem]:
        with self.lock:
            if not self.path.exists():
                return []
            return [NotificationItem.from_payload(item)
                    for item in self._read()["items"]]

    def save(self, item: NotificationItem) -> NotificationItem:
        with self.lock:
            value: _OutboxPayload = self._read() if self.path.exists() else {
                "schema_version": SCHEMA_VERSION, "items": []}
            items = [entry for entry in value["items"]
                     if entry.get("notification_id") != item.notification_id]
            items.append(item.payload())
            value["items"] = sorted(items, key=lambda entry: entry["created_at"])
            self._write(value)
            return item

    def _read(self) -> _OutboxPayload:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Notification Outbox 格式无效")
        items = value.get("items")
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("Notification Outbox 格式无效")
        return cast(_OutboxPayload, value)

    def _write(self, value: _OutboxPayload) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        temporary = self.path.with_name(self.path.name + ".tmp")
        descriptor = os.open(
            temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
