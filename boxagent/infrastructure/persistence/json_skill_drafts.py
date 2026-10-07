"""Durable pending Skill drafts controlled by the BoxAgent host."""

import json
import os
import re
import threading
from pathlib import Path

from boxagent.domain.skill import SkillDraft


DRAFT_ID = re.compile(r"^skd_[a-f0-9]{16}$")


class JsonSkillDraftRepository:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()

    def save(self, draft: SkillDraft) -> SkillDraft:
        with self.lock:
            path = self._path(draft.draft_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(draft.payload(), stream, ensure_ascii=False,
                          separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return draft

    def get(self, draft_id: str) -> SkillDraft:
        with self.lock:
            path = self._path(draft_id)
            if not path.is_file():
                raise FileNotFoundError(f"Skill 草稿不存在：{draft_id}")
            return SkillDraft.from_payload(json.loads(
                path.read_text(encoding="utf-8")))

    def list(self, *, session_id: str = "", statuses: tuple[str, ...] = ()):
        expected = {str(item) for item in statuses}
        with self.lock:
            drafts = []
            if not self.root.is_dir():
                return drafts
            for path in self.root.glob("skd_*.json"):
                try:
                    draft = SkillDraft.from_payload(json.loads(
                        path.read_text(encoding="utf-8")))
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                if session_id and draft.session_id != session_id:
                    continue
                if expected and draft.status.value not in expected:
                    continue
                drafts.append(draft)
            return sorted(drafts, key=lambda item: item.created_at)

    def latest(self, *, session_id: str, statuses: tuple[str, ...]):
        drafts = self.list(session_id=session_id, statuses=statuses)
        return drafts[-1] if drafts else None

    def _path(self, draft_id: str) -> Path:
        if not isinstance(draft_id, str) or not DRAFT_ID.fullmatch(draft_id):
            raise ValueError("Skill 草稿 ID 无效")
        path = (self.root / f"{draft_id}.json").resolve()
        if path.parent != self.root:
            raise ValueError("Skill 草稿路径越界")
        return path
