"""Product-level Skill records independent from a concrete Agent Runtime."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SkillSource(StrEnum):
    BUILTIN = "builtin"
    USER = "user"


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    instructions: str
    path: Path
    source: SkillSource
    enabled: bool

    @property
    def manifest_path(self) -> Path:
        return self.path / "SKILL.md"

    def payload(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "path": str(self.path),
            "source": self.source.value,
            "enabled": self.enabled,
        }
