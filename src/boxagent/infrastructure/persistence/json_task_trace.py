"""Bounded reader for completed task evidence used by Skill authoring."""

import json
import re
from collections.abc import Iterable
from pathlib import Path

from boxagent.core.errors import redact

TASK_ID = re.compile(r"^[a-f0-9]{12}$")
RELEVANT_EVENTS = {
    "task_started", "phase", "action", "tool_result", "tool_exception",
    "agent_message", "task_failed", "task_ended",
}


class JsonTaskTraceReader:
    def __init__(
        self, root: Path, *, character_limit: int = 32_000,
        fallback_roots: Iterable[str | Path] = (),
    ) -> None:
        self.root = Path(root).resolve()
        self.fallback_roots = tuple(Path(item).resolve() for item in fallback_roots)
        self.character_limit = max(1_000, int(character_limit))

    def read(self, task_id: str) -> dict[str, object]:
        if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
            return {}
        directory = self._find_directory(task_id)
        if directory is None:
            return {}
        result = self._read_json(directory / "result.json")
        events = []
        path = directory / "events.jsonl"
        if path.is_file():
            remaining = self.character_limit
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    event: object = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("kind") not in RELEVANT_EVENTS:
                    continue
                redacted = redact(event)
                if not isinstance(redacted, dict):
                    continue
                encoded = json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))
                if len(encoded) > 4_000:
                    redacted = {key: value for key, value in redacted.items()
                             if key not in {"text", "traceback", "content"}}
                    redacted["detail_truncated"] = True
                    encoded = json.dumps(redacted, ensure_ascii=False,
                                         separators=(",", ":"))
                if len(encoded) > remaining:
                    break
                events.append(redacted)
                remaining -= len(encoded)
        return {"task_id": task_id, "result": redact(result), "events": events}

    def _find_directory(self, task_id: str) -> Path | None:
        for root in (self.root, *self.fallback_roots):
            direct = (root / task_id).resolve()
            if direct.parent == root and direct.is_dir():
                return direct
            if not root.is_dir():
                continue
            session_matches = sorted(root.glob(f"ses_*/tasks/{task_id}"), reverse=True)
            for candidate in session_matches:
                if candidate.resolve().is_relative_to(root):
                    return candidate.resolve()
            # Canonical runs are partitioned by date and named HHMMSS-task_id.
            matches = sorted(root.glob(f"????-??-??/??????-{task_id}"), reverse=True)
            for candidate in matches:
                if candidate.resolve().is_relative_to(root):
                    return candidate.resolve()
        return None

    @staticmethod
    def _read_json(path: Path) -> object:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
