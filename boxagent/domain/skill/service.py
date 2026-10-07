"""Skill lifecycle and runtime projection policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from boxagent.domain.skill.contracts import SkillRepository
from boxagent.domain.skill.models import SkillRecord


@dataclass(frozen=True)
class SkillRuntimePolicy:
    roots: tuple[Path, ...]
    enabled_paths: frozenset[Path]
    signature: tuple[str, ...]


class SkillService:
    """Own Skill CRUD and expose an allowlist to Agent Runtime adapters."""

    def __init__(self, repository: SkillRepository):
        self.repository = repository

    def list(self) -> list[SkillRecord]:
        return self.repository.list()

    def find(self, query: str, *, limit: int = 8) -> list[SkillRecord]:
        query = str(query or "").strip().lower()
        if not query:
            return self.list()[:max(1, int(limit))]
        terms = tuple(part for part in query.replace("_", "-").split() if part)

        def score(item):
            haystack = "\n".join((
                item.skill_id, item.name, item.description, item.instructions,
            )).lower().replace("_", "-")
            exact = 20 if query in haystack else 0
            matched = sum(1 for term in terms if term in haystack)
            return exact + matched

        ranked = [(score(item), item) for item in self.list() if item.enabled]
        return [item for value, item in sorted(
            (entry for entry in ranked if entry[0] > 0),
            key=lambda entry: (-entry[0], entry[1].skill_id),
        )[:max(1, int(limit))]]

    def create(self, skill_id: str, *, name: str, description: str,
               instructions: str) -> SkillRecord:
        return self.repository.create(
            skill_id, name=name, description=description,
            instructions=instructions)

    def validate(self, skill_id: str, *, name: str, description: str,
                 instructions: str) -> None:
        self.repository.validate(
            skill_id, name=name, description=description,
            instructions=instructions)

    def update(self, skill_id: str, *, name: str, description: str,
               instructions: str) -> SkillRecord:
        return self.repository.update(
            skill_id, name=name, description=description,
            instructions=instructions)

    def set_enabled(self, skill_id: str, enabled: bool) -> SkillRecord:
        return self.repository.set_enabled(skill_id, bool(enabled))

    def delete(self, skill_id: str) -> None:
        self.repository.delete(skill_id)

    def runtime_policy(self) -> SkillRuntimePolicy:
        skills = self.repository.list()
        enabled = [item for item in skills if item.enabled]
        return SkillRuntimePolicy(
            roots=tuple(path.resolve() for path in self.repository.roots()),
            enabled_paths=frozenset(
                item.manifest_path.resolve() for item in enabled),
            signature=tuple(sorted(
                f"{item.manifest_path.resolve()}:{hashlib.sha256(item.manifest_path.read_bytes()).hexdigest()}"
                for item in enabled)),
        )
