"""Versioned product-session records owned by BoxAgent."""

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    title: str
    created_at: float
    updated_at: float
    archived: bool = False

    def payload(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的 Session metadata 版本")
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class ProductEvent:
    sequence: int
    event_id: str
    type: str
    session_id: str
    occurred_at: float
    interaction_id: str | None = None
    task_id: str | None = None
    runtime: str | None = None
    role: str | None = None
    content: str | None = None
    source: str | None = None
    status: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的 Product Event 版本")
        fields = cls.__dataclass_fields__
        return cls(**{key: value.get(key) for key in fields})


@dataclass(frozen=True)
class RuntimeBinding:
    runtime: str
    provider: str
    model: str
    thread_id: str
    context_cursor: int = 0
    previous_threads: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["previous_threads"] = list(self.previous_threads)
        return value

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        data = {key: value.get(key) for key in cls.__dataclass_fields__}
        data["context_cursor"] = int(data.get("context_cursor") or 0)
        data["previous_threads"] = tuple(data.get("previous_threads") or ())
        return cls(**data)


@dataclass(frozen=True)
class ContextCheckpoint:
    """Portable, model-generated context used to restore a non-native Runtime."""

    checkpoint_id: str
    session_id: str
    runtime: str
    source_from_sequence: int
    covered_through_sequence: int
    source_hash: str
    created_at: float
    provider: str
    model: str
    content: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的 Context Checkpoint 版本")
        fields = cls.__dataclass_fields__
        data = {key: value.get(key) for key in fields}
        data["source_from_sequence"] = int(data.get("source_from_sequence") or 0)
        data["covered_through_sequence"] = int(
            data.get("covered_through_sequence") or 0)
        data["content"] = dict(data.get("content") or {})
        return cls(**data)


@dataclass(frozen=True)
class RuntimeContextSegment:
    """A closed range of Product Events represented by one checkpoint."""

    segment_id: str
    session_id: str
    runtime: str
    start_sequence: int
    end_sequence: int
    checkpoint_id: str
    created_at: float

    def payload(self) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    @classmethod
    def from_payload(cls, value: dict[str, Any]):
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的 Runtime Context Segment 版本")
        fields = cls.__dataclass_fields__
        data = {key: value.get(key) for key in fields}
        data["start_sequence"] = int(data.get("start_sequence") or 0)
        data["end_sequence"] = int(data.get("end_sequence") or 0)
        return cls(**data)


@dataclass(frozen=True)
class InteractionContext:
    session: SessionMetadata
    interaction_id: str
    user_event: ProductEvent
    prior_messages: tuple[ProductEvent, ...] = ()
    runtime_binding: RuntimeBinding | None = None
