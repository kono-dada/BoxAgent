"""Ports for per-Session Context Ledgers."""

from typing import Any, Protocol

from boxagent.domain.context.models import LedgerEvent


class ContextLedgerStore(Protocol):
    def append(self, session_id: str, event_type: str,
               data: dict[str, Any]) -> LedgerEvent: ...

    def read(self, session_id: str) -> list[LedgerEvent]: ...


class ContextObserver(Protocol):
    """Host-side turn facts that the provider wire traffic cannot reveal.

    Implementations must never raise: accounting is not allowed to affect a turn.
    """

    def turn_started(self, session_id: str, *, channel: str) -> None: ...

    def turn_prepared(self, session_id: str, **data: Any) -> None: ...


class NullContextObserver:
    def turn_started(self, session_id, *, channel):
        return None

    def turn_prepared(self, session_id, **data):
        return None
