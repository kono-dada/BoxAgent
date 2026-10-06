"""Application-level coordination for the BoxAgent product."""

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.context_checkpoint import ContextCheckpointCoordinator
from boxagent.application.memory_context import MemoryContextProvider
from boxagent.application.memory_extraction import MemoryExtractionCoordinator

__all__ = [
    "BoxAgentApplication", "ContextCheckpointCoordinator", "MemoryContextProvider",
    "MemoryExtractionCoordinator",
]
