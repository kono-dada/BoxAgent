"""Bounded reader for completed task evidence used by Skill authoring."""

import json
import re
from pathlib import Path

from boxagent.core.errors import redact


TASK_ID = re.compile(r"^[a-f0-9]{12}$")
RELEVANT_EVENTS = {
    "task_started", "phase", "action", "tool_result", "tool_exception",
    "agent_message", "task_failed", "task_ended",
}


class JsonTaskTraceReader:
    def __init__(self, root: Path, *, character_limit=32_000,
                 fallback_roots=()):
        self.root = Path(root).resolve()
        self.fallback_roots = tuple(Path(item).resolve() for item in fallback_roots)
        self.character_limit = max(1_000, int(character_limit))

    def read(self, task_id: str) -> dict:
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
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("kind") not in RELEVANT_EVENTS:
                    continue
                event = redact(event)
                encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                if len(encoded) > 4_000:
                    event = {key: value for key, value in event.items()
                             if key not in {"text", "traceback", "content"}}
                    event["detail_truncated"] = True
                    encoded = json.dumps(event, ensure_ascii=False,
                                         separators=(",", ":"))
                if len(encoded) > remaining:
                    break
                events.append(event)
                remaining -= len(encoded)
        return {"task_id": task_id, "result": redact(result), "events": events}

    def _find_directory(self, task_id):
        for root in (self.root, *self.fallback_roots):
            direct = (root / task_id).resolve()
            if direct.parent == root and direct.is_dir():
                return direct
            if not root.is_dir():
                continue
            # Canonical runs are partitioned by date and named HHMMSS-task_id.
            matches = sorted(root.glob(f"????-??-??/??????-{task_id}"), reverse=True)
            if matches:
                return matches[0].resolve()
        return None

    @staticmethod
    def _read_json(path: Path):
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
