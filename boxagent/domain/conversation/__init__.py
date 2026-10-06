"""Conversation records and Product Session lifecycle."""

from boxagent.domain.conversation.contracts import (
    EmptyMemoryEvidenceProvider,
    NullContextCheckpointSink,
    NullMemoryExtractionSink,
)
from boxagent.domain.conversation.models import (
    ContextCheckpoint,
    InteractionContext,
    ProductEvent,
    RuntimeBinding,
    RuntimeContextSegment,
    SessionMetadata,
)
from boxagent.domain.conversation.service import ConversationService

__all__ = [
    "ContextCheckpoint", "ConversationService", "EmptyMemoryEvidenceProvider",
    "InteractionContext", "NullContextCheckpointSink", "NullMemoryExtractionSink",
    "ProductEvent", "RuntimeBinding", "RuntimeContextSegment", "SessionMetadata",
]
