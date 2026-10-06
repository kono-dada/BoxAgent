"""Canonical memory and extraction records owned by BoxAgent."""

from dataclasses import asdict, dataclass, field
from typing import Any

MEMORY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    timestamp: str | None = None


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    subject: str
    kind: str
    durability: str
    operation: str
    confidence: float
    sensitivity: str
    evidence: tuple[dict[str, str], ...] = ()
    expires_at: str | None = None
    canonical_slot: str = ""

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        data = {key: value.get(key) for key in cls.__dataclass_fields__}
        data["confidence"] = float(data.get("confidence") or 0)
        data["evidence"] = tuple(dict(item) for item in data.get("evidence") or ())
        return cls(**data)


@dataclass(frozen=True)
class CanonicalMemory:
    memory_id: str
    revision: int
    content: str
    subject: str
    kind: str
    status: str
    confidence: float
    sensitivity: str
    source_mode: str
    source_session_id: str
    source_interaction_id: str
    source_event_ids: tuple[str, ...]
    created_at: float
    updated_at: float
    expires_at: str | None = None
    backend_links: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    canonical_slot: str = ""
    supersedes_revision: int | None = None
    lineage_event_ids: tuple[str, ...] = ()

    def payload(self):
        value = asdict(self)
        value["source_event_ids"] = list(self.source_event_ids)
        value["backend_links"] = list(self.backend_links)
        value["lineage_event_ids"] = list(self.lineage_event_ids)
        return {"schema_version": MEMORY_SCHEMA_VERSION, **value}

    @classmethod
    def from_payload(cls, value):
        if value.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise ValueError("不支持的 Canonical Memory 版本")
        data = {key: value.get(key) for key in cls.__dataclass_fields__}
        data["revision"] = int(data.get("revision") or 1)
        data["confidence"] = float(data.get("confidence") or 0)
        data["source_event_ids"] = tuple(data.get("source_event_ids") or ())
        data["backend_links"] = tuple(data.get("backend_links") or ())
        data["metadata"] = dict(data.get("metadata") or {})
        data["lineage_event_ids"] = tuple(data.get("lineage_event_ids") or ())
        return cls(**data)


@dataclass(frozen=True)
class MemoryExtractionJob:
    job_id: str
    idempotency_key: str
    session_id: str
    interaction_id: str
    source_hash: str
    extractor_version: str
    status: str
    attempts: int
    created_at: float
    updated_at: float
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    next_retry_at: float | None = None

    def payload(self):
        return {"schema_version": MEMORY_SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, value):
        if value.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise ValueError("不支持的 Memory Extraction Job 版本")
        data = {key: value.get(key) for key in cls.__dataclass_fields__}
        data["attempts"] = int(data.get("attempts") or 0)
        data["result"] = dict(data.get("result") or {})
        if data.get("next_retry_at") is not None:
            data["next_retry_at"] = float(data["next_retry_at"])
        return cls(**data)
