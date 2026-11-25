"""Load the checked-in catalog and persist only the user's local culture preferences."""

import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from boxagent.domain.proactivity.lol_culture import CultureEntry, CultureState
from boxagent.domain.web.contracts import public_web_url


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("LoL 文化资料列表格式无效")
    return tuple(value)


def load_culture_catalog(path: Path) -> tuple[CultureEntry, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != 1
        or not isinstance(raw.get("entries"), list)
    ):
        raise ValueError("LoL 文化资料库格式无效")
    entries: list[CultureEntry] = []
    fields = (
        "id",
        "title",
        "event",
        "summary",
        "usage",
        "avoid",
        "source_url",
        "source_title",
        "source_kind",
        "checked_at",
    )
    for row in raw["entries"]:
        if not isinstance(row, dict) or any(
            not isinstance(row.get(key), str) or not row[key] for key in fields
        ):
            raise ValueError("LoL 文化条目缺少事实或来源")
        if row["source_kind"] not in {
            "official_article",
            "official_video",
            "community_glossary",
            "community_reference",
            "esports_report",
        }:
            raise ValueError("LoL 文化条目来源类型无效")
        public_web_url(row["source_url"])
        offset = row.get("video_offset_seconds")
        for flag in ("requires_lol_context", "allow_banter"):
            if flag in row and not isinstance(row[flag], bool):
                raise ValueError("LoL 文化条目语境或使用开关格式无效")
        if offset is not None and (
            not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
        ):
            raise ValueError("视频时间点无效")
        entries.append(
            CultureEntry(
                **{key: row[key] for key in fields},
                aliases=_strings(row.get("aliases")),
                players=_strings(row.get("players")),
                champions=_strings(row.get("champions")),
                video_offset_seconds=offset,
                requires_lol_context=row.get("requires_lol_context", False),
                allow_banter=row.get("allow_banter", True),
            )
        )
    if len({entry.id for entry in entries}) != len(entries):
        raise ValueError("LoL 文化条目 ID 重复")
    return tuple(entries)


class JsonCultureRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> CultureState:
        try:
            row = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return CultureState()
        if not isinstance(row, dict) or not isinstance(row.get("enabled"), bool):
            raise ValueError("本机接梗偏好格式无效")
        stamp = row.get("last_banter_at", 0)
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
            raise ValueError("本机接梗时间格式无效")
        return CultureState(
            row["enabled"],
            _strings(row.get("blocked_ids", [])),
            _strings(row.get("recent_ids", [])),
            float(stamp),
        )

    def save(self, state: CultureState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            try:
                json.dump(asdict(state), file, ensure_ascii=False)
                file.close()
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
