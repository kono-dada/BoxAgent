"""Product-level Skill records independent from a concrete Agent Runtime."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import time


class SkillSource(StrEnum):
    BUILTIN = "builtin"
    USER = "user"


class SkillDraftAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    NONE = "none"


class SkillDraftStatus(StrEnum):
    PREPARING = "preparing"
    PENDING = "pending"
    INSTALLED = "installed"
    NOT_NEEDED = "not_needed"
    FAILED = "failed"


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


@dataclass(frozen=True)
class SkillDraft:
    draft_id: str
    action: SkillDraftAction
    skill_id: str
    name: str
    description: str
    instructions: str
    rationale: str
    session_id: str = ""
    source_interaction_id: str = ""
    source_task_id: str = ""
    status: SkillDraftStatus = SkillDraftStatus.PENDING
    created_at: float = 0.0

    def payload(self, *, include_instructions=True) -> dict:
        value = {
            "draft_id": self.draft_id,
            "action": self.action.value,
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "rationale": self.rationale,
            "session_id": self.session_id,
            "source_interaction_id": self.source_interaction_id,
            "source_task_id": self.source_task_id,
            "status": self.status.value,
            "created_at": self.created_at,
        }
        if include_instructions:
            value["instructions"] = self.instructions
        return value

    @classmethod
    def from_payload(cls, value: dict):
        return cls(
            draft_id=value["draft_id"],
            action=SkillDraftAction(value["action"]),
            skill_id=value.get("skill_id", ""),
            name=value.get("name", ""),
            description=value.get("description", ""),
            instructions=value.get("instructions", ""),
            rationale=value.get("rationale", ""),
            session_id=value.get("session_id", ""),
            source_interaction_id=value.get("source_interaction_id", ""),
            source_task_id=value.get("source_task_id", ""),
            status=SkillDraftStatus(value.get("status", "pending")),
            created_at=float(value.get("created_at") or time.time()),
        )
