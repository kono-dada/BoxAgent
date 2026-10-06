"""Atomic JSON materialization of the completion notification outbox."""

import json
import os
import threading
from pathlib import Path

from boxagent.domain.notification import NotificationItem


SCHEMA_VERSION = 1


class JsonNotificationRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            if not self.path.exists():
                self._write({"schema_version": SCHEMA_VERSION, "items": []})
            else:
                self._read()

    def list(self):
        with self.lock:
            if not self.path.exists():
                return []
            return [NotificationItem.from_payload(item)
                    for item in self._read()["items"]]

    def save(self, item):
        with self.lock:
            value = self._read() if self.path.exists() else {
                "schema_version": SCHEMA_VERSION, "items": []}
            items = [entry for entry in value["items"]
                     if entry.get("notification_id") != item.notification_id]
            items.append(item.payload())
            value["items"] = sorted(items, key=lambda entry: entry["created_at"])
            self._write(value)
            return item

    def _read(self):
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if value.get("schema_version") != SCHEMA_VERSION \
                or not isinstance(value.get("items"), list):
            raise ValueError("Notification Outbox 格式无效")
        return value

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
