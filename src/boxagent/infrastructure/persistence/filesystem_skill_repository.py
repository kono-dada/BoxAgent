"""Filesystem-backed Skill repository for builtin and user Skills."""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from pathlib import Path

from boxagent.domain.skill.models import SkillRecord, SkillSource


SCHEMA_VERSION = 1
SKILL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillFileRepository:
    def __init__(self, *, builtin_root: Path, user_root: Path):
        self.builtin_root = Path(builtin_root).resolve()
        self.user_root = Path(user_root).resolve()
        self.registry_path = self.user_root / "registry.json"
        self.lock = threading.RLock()

    def list(self) -> list[SkillRecord]:
        with self.lock:
            self._ensure()
            registry = self._read_registry()
            records = [
                *self._scan(self.builtin_root, SkillSource.BUILTIN, registry),
                *self._scan(self.user_root, SkillSource.USER, registry),
            ]
            identifiers = [item.skill_id for item in records]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("内置 Skill 与用户 Skill 的 ID 不能重复")
            return sorted(records, key=lambda item: (item.source.value, item.skill_id))

    def create(self, skill_id: str, *, name: str, description: str,
               instructions: str) -> SkillRecord:
        with self.lock:
            target = self._user_skill_path(skill_id)
            if target.exists() or (self.builtin_root / skill_id).exists():
                raise ValueError(f"Skill 已存在：{skill_id}")
            content = self._render(name, description, instructions)
            target.mkdir(parents=False)
            try:
                self._atomic_write(target / "SKILL.md", content)
            except BaseException:
                shutil.rmtree(target, ignore_errors=True)
                raise
            self._set_registry(skill_id, True)
            return self._read_record(target, SkillSource.USER, True)

    def validate(self, skill_id: str, *, name: str, description: str,
                 instructions: str) -> None:
        with self.lock:
            self._user_skill_path(skill_id)
            self._render(name, description, instructions)

    def update(self, skill_id: str, *, name: str, description: str,
               instructions: str) -> SkillRecord:
        with self.lock:
            target = self._user_skill_path(skill_id)
            if not target.is_dir():
                raise FileNotFoundError(f"用户 Skill 不存在：{skill_id}")
            self._atomic_write(
                target / "SKILL.md", self._render(name, description, instructions))
            enabled = self._read_registry()["skills"].get(skill_id, {}).get("enabled", True)
            return self._read_record(target, SkillSource.USER, enabled)

    def set_enabled(self, skill_id: str, enabled: bool) -> SkillRecord:
        with self.lock:
            records = {item.skill_id: item for item in self.list()}
            if skill_id not in records:
                raise FileNotFoundError(f"Skill 不存在：{skill_id}")
            self._set_registry(skill_id, enabled)
            item = records[skill_id]
            return SkillRecord(
                skill_id=item.skill_id, name=item.name,
                description=item.description, instructions=item.instructions,
                path=item.path,
                source=item.source, enabled=enabled)

    def delete(self, skill_id: str) -> None:
        with self.lock:
            target = self._user_skill_path(skill_id)
            if not target.exists() and not target.is_symlink():
                raise FileNotFoundError(f"用户 Skill 不存在：{skill_id}")
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
            registry = self._read_registry()
            registry["skills"].pop(skill_id, None)
            self._write_registry(registry)

    def roots(self) -> tuple[Path, ...]:
        with self.lock:
            self._ensure()
            return self.builtin_root, self.user_root

    def _scan(self, root: Path, source: SkillSource, registry) -> list[SkillRecord]:
        if not root.is_dir():
            return []
        records = []
        for path in root.iterdir():
            if (path.is_symlink() or not path.is_dir()
                    or path.name.startswith(".")
                    or not SKILL_ID.fullmatch(path.name)):
                continue
            manifest = path / "SKILL.md"
            if manifest.is_symlink() or not manifest.is_file():
                continue
            try:
                manifest.resolve().relative_to(root)
            except ValueError:
                continue
            enabled = registry["skills"].get(path.name, {}).get("enabled", True)
            records.append(self._read_record(path, source, enabled))
        return records

    def _read_record(self, path: Path, source: SkillSource, enabled: bool) -> SkillRecord:
        name, description, instructions = self._parse_manifest(
            (path / "SKILL.md").read_text(encoding="utf-8"))
        return SkillRecord(
            skill_id=path.name, name=name, description=description,
            instructions=instructions,
            path=path.resolve(), source=source, enabled=enabled)

    @staticmethod
    def _parse_manifest(content: str) -> tuple[str, str, str]:
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError("SKILL.md 必须以 YAML frontmatter 开始")
        fields = {}
        closed = False
        closing_index = None
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                closed = True
                closing_index = index
                break
            key, separator, value = line.partition(":")
            if separator and key.strip() in {"name", "description"}:
                raw = value.strip()
                try:
                    fields[key.strip()] = json.loads(raw)
                except json.JSONDecodeError:
                    fields[key.strip()] = raw.strip("\"'")
        if not closed:
            raise ValueError("SKILL.md frontmatter 未结束")
        if not isinstance(fields.get("name"), str) or not fields["name"].strip():
            raise ValueError("SKILL.md 缺少 name")
        if not isinstance(fields.get("description"), str) or not fields["description"].strip():
            raise ValueError("SKILL.md 缺少 description")
        instructions = "\n".join(lines[closing_index + 1:]).strip()
        return (fields["name"].strip(), fields["description"].strip(),
                instructions)

    @staticmethod
    def _render(name: str, description: str, instructions: str) -> str:
        values = {
            "name": SkillFileRepository._require_text(name, "name", 128),
            "description": SkillFileRepository._require_text(
                description, "description", 1024),
            "instructions": SkillFileRepository._require_text(
                instructions, "instructions", 100_000),
        }
        return (
            "---\n"
            f"name: {json.dumps(values['name'], ensure_ascii=False)}\n"
            f"description: {json.dumps(values['description'], ensure_ascii=False)}\n"
            "---\n\n"
            f"{values['instructions']}\n"
        )

    @staticmethod
    def _require_text(value, name, limit):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} 不能为空")
        cleaned = value.strip()
        if len(cleaned) > limit:
            raise ValueError(f"{name} 超过长度限制")
        return cleaned

    def _user_skill_path(self, skill_id: str) -> Path:
        if not isinstance(skill_id, str) or not SKILL_ID.fullmatch(skill_id):
            raise ValueError("Skill ID 只能包含小写字母、数字、下划线和连字符")
        self._ensure()
        target = self.user_root / skill_id
        if target.resolve().parent != self.user_root:
            raise ValueError("Skill 路径越界")
        return target

    def _ensure(self):
        self.user_root.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self._write_registry(self._empty_registry())

    def _set_registry(self, skill_id: str, enabled: bool):
        registry = self._read_registry()
        registry["skills"][skill_id] = {"enabled": bool(enabled)}
        self._write_registry(registry)

    def _read_registry(self):
        if not self.registry_path.exists():
            return self._empty_registry()
        value = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if value.get("schema_version") != SCHEMA_VERSION \
                or not isinstance(value.get("skills"), dict):
            raise ValueError("Skill registry 格式无效")
        return value

    def _write_registry(self, value):
        self._atomic_write(
            self.registry_path,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")

    @staticmethod
    def _empty_registry():
        return {"schema_version": SCHEMA_VERSION, "skills": {}}

    @staticmethod
    def _atomic_write(path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
