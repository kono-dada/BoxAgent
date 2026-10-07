"""Dependency-free shared identifiers, events, states, and errors."""

from boxagent.core.errors import TaskFailure
from boxagent.core.states import Snapshot, presentation_state

__all__ = ["Snapshot", "TaskFailure", "presentation_state"]
