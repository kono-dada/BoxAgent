"""Conversation records and Product Session lifecycle."""

from boxagent.domain.conversation.contracts import (
    InteractionFinalizationSink,
    NullContextCheckpointSink,
    UserMessageCommitSink,
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
    "ContextCheckpoint", "ConversationService", "InteractionFinalizationSink",
    "InteractionContext", "NullContextCheckpointSink",
    "ProductEvent", "RuntimeBinding", "RuntimeContextSegment", "SessionMetadata",
    "UserMessageCommitSink",
]
