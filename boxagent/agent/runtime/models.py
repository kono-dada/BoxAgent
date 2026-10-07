"""Data passed into and out of an Agent Runtime."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelProfile:
    """Describe how the Codex runtime reaches one model provider."""

    provider: str
    model: str
    display_name: str
    base_url: str | None = None
    api_key_env: str | None = None
    api_key: str = field(default="", repr=False, compare=False)
    model_catalog: Path | None = None

    @property
    def is_custom_provider(self) -> bool:
        return self.provider != "openai"


@dataclass(frozen=True)
class RuntimeMessage:
    """One product conversation message projected into Runtime history."""

    role: str
    content: str
    sequence: int = 0
    event_id: str = ""
    source: str = ""


@dataclass(frozen=True)
class RuntimeRequest:
    """One fully compiled request handed from the Harness to a Runtime."""

    query: str
    developer_instructions: str
    output_schema: dict[str, Any]
    persona_source: str | None = None
    history: tuple[RuntimeMessage, ...] = ()
    history_delta: tuple[RuntimeMessage, ...] = ()
    evidence_context: str = ""


@dataclass(frozen=True)
class RuntimeRestoreContext:
    """Portable recovery material for a newly created Runtime session."""

    checkpoint: str = ""
    messages: tuple[RuntimeMessage, ...] = ()
