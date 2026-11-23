"""Sanitize backend records before returning them to voice or UI."""

from collections.abc import Iterable
from typing import NotRequired, TypedDict


class PublicMemoryItem(TypedDict):
    id: str | None
    content: str | None
    timestamp: object
    type: str
    metadata: NotRequired[dict[str, object]]


def public_memory_items(items: Iterable[object]) -> list[PublicMemoryItem]:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = dict(item.get("metadata") or {})
        identity = item.get("id")
        content = item.get("content")
        public: PublicMemoryItem = {
            "id": identity if isinstance(identity, str) else None,
            "content": content if isinstance(content, str) else None,
            "timestamp": item.get("timestamp"),
            "type": str(item.get("type") or metadata.get("node_type") or "EVENT"),
        }
        safe_metadata = {
            key: metadata.get(key) for key in (
                "source", "session_id", "interaction_id",
                "source_event_id", "source_event_ids", "narrative_level",
                "checkpoint_id", "jev_mem", "modality", "evidence_id", "episode_id",
                "evidence_kind", "game", "subject", "aspect", "variant", "game_version",
                "captured_at", "chain_key") if key in metadata
        }
        if safe_metadata:
            public["metadata"] = safe_metadata
        result.append(public)
    return result
