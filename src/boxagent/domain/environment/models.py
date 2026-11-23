"""Environment value objects safe to expose to an Agent Runtime."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class EnvironmentContext:
    """One immutable, non-sensitive snapshot of the local host."""

    captured_at: str
    timezone: str
    weekday: str

    def payload(self) -> dict[str, str]:
        return asdict(self)
