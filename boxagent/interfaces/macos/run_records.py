"""Build a Finder-friendly, Session-scoped view of task run records."""

from __future__ import annotations

import json
import os
from pathlib import Path


class SessionRunRecords:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def directories(self, session_id: str) -> list[Path]:
        if not self._valid_session_id(session_id):
            return []
        matches: dict[str, Path] = {}
        patterns = (
            "????-??-??/??????-*/manifest.json",
            f"sessions/{session_id}/????-??-??/??????-*/manifest.json",
        )
        manifests = [p for pattern in patterns for p in self.root.glob(pattern)]
        data = self.root.parent
        unified_manifests = list((data / "sessions" / session_id / "tasks").glob("*/manifest.json"))
        # Both layouts remain auditable after rollback; prefer its current copy.
        if (data / "sessions/.rollback-v1").exists():
            manifests = unified_manifests + manifests
        else:
            manifests.extend(unified_manifests)
        for manifest in manifests:
            if not manifest.resolve().is_relative_to(data):
                continue
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            if isinstance(value, dict) and value.get("session_id") == session_id:
                identity = str(value.get("task_id") or manifest.parent)
                matches[identity] = manifest.parent.resolve()
        return sorted(set(matches.values()), key=self._sort_key)

    def latest(self, session_id: str) -> Path | None:
        directories = self.directories(session_id)
        return directories[-1] if directories else None

    def materialize_view(self, session_id: str) -> Path:
        if not self._valid_session_id(session_id):
            return self.root
        view = self.root / "by-session" / session_id
        view.mkdir(parents=True, exist_ok=True)
        expected = set()
        for directory in self.directories(session_id):
            date = directory.parent.name
            name = f"{date}-{directory.name}"
            expected.add(name)
            link = view / name
            if link.is_symlink() and link.resolve(strict=False) == directory:
                continue
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                continue
            temporary = view / f".{name}.tmp"
            temporary.unlink(missing_ok=True)
            temporary.symlink_to(os.path.relpath(directory, view),
                                 target_is_directory=True)
            temporary.replace(link)
        for link in view.iterdir():
            if link.is_symlink() and link.name not in expected:
                link.unlink(missing_ok=True)
        return view

    @staticmethod
    def _sort_key(directory: Path):
        manifest = directory / "manifest.json"
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
            return float(value.get("started_at") or 0), str(directory)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return 0.0, str(directory)

    @staticmethod
    def _valid_session_id(session_id):
        return (isinstance(session_id, str) and session_id.startswith("ses_")
                and "/" not in session_id and ".." not in session_id)
