"""Perception value objects."""

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowIdentity:
    id: int
    app: str
