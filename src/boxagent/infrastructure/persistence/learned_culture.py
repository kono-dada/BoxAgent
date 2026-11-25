"""Atomic local research notes; never modifies the shared curated catalog."""

import hashlib
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from boxagent.domain.proactivity.learned_culture import CultureEvidence, LearnedCultureNote
from boxagent.domain.web.contracts import public_web_url


class JsonLearnedCultureRepository:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, topic: str) -> Path:
        return self.root / (hashlib.sha256(topic.encode()).hexdigest() + ".json")

    def load(self, topic: str) -> LearnedCultureNote | None:
        path = self._path(topic)
        try:
            if path.stat().st_size > 16000:
                return None
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or row.get("topic") != topic:
                return None
            evidence = tuple(
                CultureEvidence(public_web_url(item["url"]), item["title"], item["excerpt"])
                for item in row["evidence"]
            )
            if not 1 <= len(evidence) <= 2 or any(
                not isinstance(item.title, str)
                or not isinstance(item.excerpt, str)
                or not 1 <= len(item.excerpt) <= 180
                for item in evidence
            ):
                return None
            return LearnedCultureNote(
                topic, evidence, float(row["retrieved_at"]), float(row["expires_at"])
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, note: LearnedCultureNote) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.root, delete=False
        ) as file:
            temporary = Path(file.name)
            try:
                json.dump(asdict(note), file, ensure_ascii=False)
                file.close()
                temporary.replace(self._path(note.topic))
            finally:
                temporary.unlink(missing_ok=True)
