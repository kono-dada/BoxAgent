"""Persistence and optional memory ports for Product Sessions."""

from typing import Protocol

from boxagent.domain.conversation.models import (
    ContextCheckpoint,
    ProductEvent,
    RuntimeBinding,
    RuntimeContextSegment,
    SessionMetadata,
)


class SessionStore(Protocol):
    async def start(self) -> None: ...
    async def create_session(self, title: str = "新会话") -> SessionMetadata: ...
    async def list_sessions(self, *, include_archived: bool = False) -> list[SessionMetadata]: ...
    async def active_session(self) -> SessionMetadata | None: ...
    async def activate_session(self, session_id: str) -> SessionMetadata: ...
    async def archive_session(self, session_id: str) -> SessionMetadata: ...
    async def rename_session(self, session_id: str, title: str) -> SessionMetadata: ...
    async def append_event(self, event: ProductEvent) -> ProductEvent: ...
    async def read_events(self, session_id: str, *, after_sequence: int = 0,
                          limit: int | None = None) -> list[ProductEvent]: ...
    async def runtime_binding(self, session_id: str, runtime: str) -> RuntimeBinding | None: ...
    async def save_runtime_binding(self, session_id: str, binding: RuntimeBinding) -> None: ...
    async def latest_checkpoint(self, session_id: str,
                                runtime: str) -> ContextCheckpoint | None: ...
    async def append_checkpoint(self, checkpoint: ContextCheckpoint,
                                segment: RuntimeContextSegment) -> ContextCheckpoint: ...
    async def context_segments(self, session_id: str,
                               runtime: str) -> list[RuntimeContextSegment]: ...


class ContextCheckpointGenerator(Protocol):
    provider: str
    model: str

    async def generate(self, *, previous: ContextCheckpoint | None,
                       messages: tuple[ProductEvent, ...]) -> dict: ...


class ContextCheckpointSink(Protocol):
    async def interaction_finalized(self, *, session_id: str, interaction_id: str,
                                    source_hash: str) -> None: ...


class UserMessageCommitSink(Protocol):
    """Observe a durable Final User Message without coupling to a feature."""

    async def user_message_committed(self, *, session_id: str,
                                     interaction_id: str,
                                     event: ProductEvent,
                                     source_hash: str) -> None: ...


class InteractionFinalizationSink(Protocol):
    """Observe a sealed interaction without implying successful execution."""

    async def interaction_finalized(self, *, session_id: str, interaction_id: str,
                                    source_hash: str) -> None: ...


class NullContextCheckpointSink:
    async def interaction_finalized(self, *, session_id: str, interaction_id: str,
                                    source_hash: str) -> None:
        del session_id, interaction_id, source_hash
