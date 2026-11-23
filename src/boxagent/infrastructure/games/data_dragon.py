"""Look up localized, versioned champion and item facts on Riot's static CDN."""

import asyncio
import json
import re
import tempfile
import time
import unicodedata
from html import unescape
from pathlib import Path
from urllib.request import urlopen

from boxagent.domain.proactivity.lol_knowledge import LolKnowledgeEntry, LolKnowledgeKind

BASE_URL = "https://ddragon.leagueoflegends.com"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_CHAMPION_ID = re.compile(r"[A-Za-z0-9_]+")
_MAX_BYTES = 8 * 1024 * 1024


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _plain(value: object) -> str:
    without_tags = re.sub(r"<[^>]*>", " ", _text(value))
    return " ".join(unescape(without_tags).split())[:420]


def _key(value: object) -> str:
    return "".join(unicodedata.normalize("NFKC", _text(value)).casefold().split())


class DataDragonKnowledge:
    def __init__(self, cache_dir: Path, *, version: str = "", locale: str = "zh_CN") -> None:
        self.cache_dir = cache_dir
        self.version = version
        self.locale = locale if locale in ("zh_CN", "zh_TW", "en_US") else "zh_CN"
        self.latest: tuple[str, float] | None = None
        self.lock = asyncio.Lock()

    async def lookup(self, kind: LolKnowledgeKind, name: str) -> LolKnowledgeEntry | None:
        async with self.lock:
            return await asyncio.to_thread(self._lookup, kind, name)

    def _lookup(self, kind: LolKnowledgeKind, name: str) -> LolKnowledgeEntry | None:
        version = self._catalog_version()
        needle = _key(name)
        for locale in dict.fromkeys((self.locale, "zh_CN", "zh_TW")):
            prefix = f"cdn/{version}/data/{locale}"
            relative = f"{prefix}/{kind}.json"
            catalog = self._catalog(relative)
            matches: list[tuple[str, dict[str, object]]] = []
            for ident, value in catalog.items():
                row = _object(value)
                if needle in {
                    _key(ident),
                    _key(row.get("name")),
                    _key(row.get("title")),
                    _key(row.get("key")),
                }:
                    matches.append((ident, row))
            if len(matches) != 1:
                continue
            ident, row = matches[0]
            if kind == "champion":
                if not _CHAMPION_ID.fullmatch(ident):
                    continue
                relative = f"{prefix}/champion/{ident}.json"
                row = _object(self._catalog(relative).get(ident))
                details = self._abilities(row)
            else:
                details = {
                    "description": _plain(row.get("description")) or _plain(row.get("plaintext"))
                }
            if row.get("name") and any(details.values()):
                return LolKnowledgeEntry(
                    _text(row["name"]), details, version, locale, f"{BASE_URL}/{relative}"
                )
        return None

    @staticmethod
    def _abilities(row: dict[str, object]) -> dict[str, str]:
        details: dict[str, str] = {}
        passive = _object(row.get("passive"))
        spells = row.get("spells")
        entries = [passive, *(spells if isinstance(spells, list) else [])]
        for slot, value in zip(("被动", "Q", "W", "E", "R"), entries, strict=False):
            ability = _object(value)
            if description := _plain(ability.get("description")):
                details[slot] = f"{_plain(ability.get('name'))}：{description}"
        return details

    def _catalog_version(self) -> str:
        if self.version:
            if not _VERSION.fullmatch(self.version):
                raise ValueError("无效的 Data Dragon 版本")
            return self.version
        now = time.monotonic()
        if self.latest is not None and now < self.latest[1]:
            return self.latest[0]
        versions = self._download("api/versions.json")
        version = (
            next(
                (
                    value
                    for value in versions
                    if isinstance(value, str) and _VERSION.fullmatch(value)
                ),
                "",
            )
            if isinstance(versions, list)
            else ""
        )
        if not version:
            raise ValueError("Data Dragon 版本列表为空")
        self.latest = (version, now + 3600)
        return version

    def _catalog(self, relative: str) -> dict[str, object]:
        path = self.cache_dir / relative
        try:
            with path.open("rb") as file:
                raw = file.read(_MAX_BYTES + 1)
            if len(raw) <= _MAX_BYTES:
                data = _object(_object(json.loads(raw)).get("data"))
                if data:
                    return data
        except (OSError, ValueError):
            pass
        payload = self._download(relative)
        data = _object(_object(payload).get("data"))
        if not data:
            raise ValueError("Data Dragon 资料为空")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary = Path(file.name)
            try:
                json.dump(payload, file, ensure_ascii=False)
                file.close()
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return data

    @staticmethod
    def _download(relative: str) -> object:
        with urlopen(f"{BASE_URL}/{relative}", timeout=5) as response:
            raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("Data Dragon 资料超出大小限制")
        return json.loads(raw)
