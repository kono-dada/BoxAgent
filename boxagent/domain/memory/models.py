"""Domain records for Jev-Mem-first memory orchestration."""

from dataclasses import asdict, dataclass, field
from typing import Any

MEMORY_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class MemoryObservation:
    observation_id: str
    source_event_id: str
    session_id: str
    interaction_id: str
    role: str
    content: str
    occurred_at: float
    channel: str
    content_hash: str


@dataclass(frozen=True)
class MemoryIngestionJob:
    job_id: str
    idempotency_key: str
    session_id: str
    interaction_id: str
    source_hash: str
    admission_version: str
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
        version = value.get("schema_version")
        if version not in {1, MEMORY_SCHEMA_VERSION}:
            raise ValueError("不支持的 Memory Ingestion Job 版本")
        data = {key: value.get(key) for key in cls.__dataclass_fields__}
        data["admission_version"] = (data.get("admission_version")
                                     or value.get("extractor_version")
                                     or "legacy")
        data["attempts"] = int(data.get("attempts") or 0)
        data["result"] = dict(data.get("result") or {})
        if data.get("next_retry_at") is not None:
            data["next_retry_at"] = float(data["next_retry_at"])
        return cls(**data)

@dataclass(frozen=True)
class ProfilePatch:
    op: str
    path: str
    value: Any = None
    confidence: float = 0.0
