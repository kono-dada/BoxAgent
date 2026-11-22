"""Environment capability required by the application layer."""

from typing import Protocol

from boxagent.domain.environment.models import EnvironmentContext


class EnvironmentProvider(Protocol):
    """Capture a privacy-bounded snapshot of the current host environment."""

    def capture(self) -> EnvironmentContext: ...
