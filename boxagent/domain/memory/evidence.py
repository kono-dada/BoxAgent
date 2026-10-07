"""Sanitize backend records before returning them to voice or UI."""


def public_memory_items(items) -> list[dict]:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = dict(item.get("metadata") or {})
        public = {
            "id": item.get("id"), "content": item.get("content"),
            "timestamp": item.get("timestamp"),
            "type": item.get("type") or metadata.get("node_type") or "EVENT",
        }
        safe_metadata = {
            key: metadata.get(key) for key in (
                "source", "session_id", "interaction_id",
                "source_event_id", "source_event_ids", "narrative_level",
                "checkpoint_id", "jev_mem") if key in metadata
        }
        if safe_metadata:
            public["metadata"] = safe_metadata
        result.append(public)
    return result
