"""Persist the most recent visible quest for returning players."""

import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from math import isfinite

from boxagent.domain.proactivity.game_entry import EntryHistory, QuestProgress, TrackedQuest

logger = logging.getLogger(__name__)


def _progress(value: object) -> QuestProgress | None:
    if not isinstance(value, dict):
        return None
    launch, title, objective, observed_at = (value.get(key) for key in ("launch", "title", "objective", "observed_at"))
    if (not isinstance(launch, str) or not launch or not isinstance(title, str)
            or not isinstance(objective, str) or not objective or type(observed_at) not in (int, float)
            or not isfinite(observed_at)):
        return None
    return QuestProgress(launch, TrackedQuest(title, objective), float(observed_at))


def _record(progress: QuestProgress | None) -> dict[str, str | float] | None:
    if progress is None:
        return None
    return {"launch": progress.launch, "title": progress.quest.title,
            "objective": progress.quest.objective, "observed_at": progress.observed_at}


class QuestProgressFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> EntryHistory:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return EntryHistory()
        except (OSError, ValueError):
            logger.warning("Could not load game progress", exc_info=True)
            return EntryHistory()
        if not isinstance(data, dict):
            return EntryHistory()
        greeted = data.get("greeted_launch")
        return EntryHistory(_progress(data.get("current")), _progress(data.get("previous")),
                            greeted if isinstance(greeted, str) else "")

    def write(self, state: EntryHistory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"current": _record(state.current), "previous": _record(state.previous),
                "greeted_launch": state.greeted_launch}
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                json.dump(data, stream, ensure_ascii=False, allow_nan=False)
                stream.close()
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
