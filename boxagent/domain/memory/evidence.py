"""Sanitize backend records before returning them to voice or UI."""


def public_memory_items(items) -> list[dict]:
    return [{key: item.get(key) for key in ("id", "content", "timestamp")}
            for item in items if isinstance(item, dict)]
