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

from boxagent.domain.proactivity.lol_basics import lookup_basic
from boxagent.domain.proactivity.lol_knowledge import LolKnowledgeEntry, LolKnowledgeKind

BASE_URL = "https://ddragon.leagueoflegends.com"
COMMUNITY_URL = "https://raw.communitydragon.org"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_CHAMPION_ID = re.compile(r"[A-Za-z0-9_]+")
_MAX_BYTES = 8 * 1024 * 1024
_CHAMPION_ALIASES = {
    "石头人": "Malphite",
    "石头": "Malphite",
    "墨菲特": "Malphite",
    "瞎子": "LeeSin",
    "盲僧": "LeeSin",
    "李青": "LeeSin",
    "女枪": "MissFortune",
    "好运姐": "MissFortune",
    "厄运小姐": "MissFortune",
    "剑圣": "MasterYi",
    "易大师": "MasterYi",
    "亚索": "Yasuo",
    "狐狸": "Ahri",
    "阿狸": "Ahri",
    "盖伦": "Garen",
    "诺手": "Darius",
    "德莱厄斯": "Darius",
    "寒冰": "Ashe",
    "艾希": "Ashe",
    "光辉": "Lux",
    "拉克丝": "Lux",
    "机器人": "Blitzcrank",
    "布里茨": "Blitzcrank",
    "锤石": "Thresh",
    "提莫": "Teemo",
    "瑞兹": "Ryze",
    "流浪": "Ryze",
    "鳄鱼": "Renekton",
    "狗头": "Nasus",
    "VN": "Vayne",
    "薇恩": "Vayne",
    "EZ": "Ezreal",
    "伊泽瑞尔": "Ezreal",
    "金克丝": "Jinx",
    "小炮": "Tristana",
}


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _rows(value: object) -> list[dict[str, object]]:
    return [_object(row) for row in value] if isinstance(value, list) else []


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
        if kind == "basic":
            return lookup_basic(name)
        version = self._catalog_version()
        needle = _key(name)
        if kind in ("champion", "champion_detail"):
            needle = _key(
                {_key(alias): ident for alias, ident in _CHAMPION_ALIASES.items()}.get(needle, name)
            )
        for locale in dict.fromkeys((self.locale, "zh_CN", "zh_TW")):
            prefix = f"cdn/{version}/data/{locale}"
            catalog_name = {
                "champion_detail": "champion",
                "summoner": "summoner",
                "rune": "runesReforged",
            }.get(kind, kind)
            relative = f"{prefix}/{catalog_name}.json"
            catalog = self._runes(relative) if kind == "rune" else self._catalog(relative)
            matches: list[tuple[str, dict[str, object]]] = []
            default_mode = False
            for ident, value in catalog.items():
                row = _object(value)
                if needle in {
                    _key(ident),
                    _key(row.get("name")),
                    _key(row.get("title")),
                    _key(row.get("key")),
                }:
                    matches.append((ident, row))
            if len(matches) > 1 and kind == "item":
                default_mode = True
                matches = [
                    (ident, row)
                    for ident, row in matches
                    if _object(row.get("maps")).get("11") is True
                ]
            if len(matches) > 1 and kind == "summoner":
                default_mode = True
                matches = [
                    (ident, row)
                    for ident, row in matches
                    if isinstance(modes := row.get("modes"), list) and "CLASSIC" in modes
                ]
            if len(matches) != 1:
                continue
            ident, row = matches[0]
            if kind == "champion_detail":
                return self._community_champion(version, locale, row)
            if kind == "champion":
                if not _CHAMPION_ID.fullmatch(ident):
                    continue
                relative = f"{prefix}/champion/{ident}.json"
                row = _object(self._catalog(relative).get(ident))
                details = self._abilities(row)
            elif kind == "summoner":
                details = {
                    "description": _plain(row.get("description")),
                    "cooldown": str(row.get("cooldownBurn", "")),
                }
            else:
                details = {
                    "description": _plain(row.get("description")) or _plain(row.get("plaintext"))
                }
            if row.get("name") and any(details.values()):
                if default_mode:
                    details["模式提示"] = "同名资料默认选召唤师峡谷；其他模式请提供准确 ID。"
                return LolKnowledgeEntry(
                    _text(row["name"]), details, version, locale, f"{BASE_URL}/{relative}"
                )
        return None

    def _runes(self, relative: str) -> dict[str, object]:
        payload = self._cached_payload(relative)
        catalog: dict[str, object] = {}
        for tree in _rows(payload):
            for slot in _rows(tree.get("slots")):
                for row in _rows(slot.get("runes")):
                    catalog[str(row.get("id"))] = {
                        **row,
                        "description": row.get("longDesc") or row.get("shortDesc"),
                    }
        return catalog

    def _community_champion(
        self, version: str, locale: str, champion: dict[str, object]
    ) -> LolKnowledgeEntry | None:
        ident = _text(champion.get("key"))
        if not ident.isdigit():
            return None
        patch = version.rsplit(".", 1)[0]
        relative = (
            f"{patch}/plugins/rcp-be-lol-game-data/global/{locale.lower()}"
            f"/v1/champions/{ident}.json"
        )
        row = _object(self._cached_payload(relative, base_url=COMMUNITY_URL))
        details = self._abilities(row)
        if not row.get("name") or not details:
            return None
        return LolKnowledgeEntry(
            _text(row["name"]),
            details,
            patch,
            locale,
            f"{COMMUNITY_URL}/{relative}",
            "CommunityDragon",
        )

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
            if cooldown := _text(ability.get("cooldownBurn")):
                details[f"{slot}基础冷却"] = cooldown + " 秒；未计算技能急速"
        return details

    def _catalog_version(self) -> str:
        if self.version:
            if not _VERSION.fullmatch(self.version):
                raise ValueError("无效的 Data Dragon 版本")
            return self.version
        now = time.monotonic()
        if self.latest is not None and now < self.latest[1]:
            return self.latest[0]
        try:
            versions = self._download("api/versions.json")
        except OSError:
            versions = self._read_cached("api/versions.json")
        else:
            if isinstance(versions, list) and any(
                isinstance(value, str) and _VERSION.fullmatch(value) for value in versions
            ):
                self._write_cached("api/versions.json", versions)
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
        data = _object(_object(self._cached_payload(relative)).get("data"))
        if not data:
            raise ValueError("Data Dragon 资料为空")
        return data

    def _read_cached(self, relative: str) -> object:
        path = self.cache_dir / relative
        try:
            with path.open("rb") as file:
                raw = file.read(_MAX_BYTES + 1)
            if len(raw) <= _MAX_BYTES:
                return json.loads(raw)
        except (OSError, ValueError):
            pass
        return None

    def _cached_payload(self, relative: str, *, base_url: str = BASE_URL) -> object:
        cache_path = f"community/{relative}" if base_url == COMMUNITY_URL else relative
        if (cached := self._read_cached(cache_path)) is not None:
            return cached
        payload = (
            self._download(relative) if base_url == BASE_URL else self._download_community(relative)
        )
        if not isinstance(payload, (dict, list)) or not payload:
            raise ValueError("游戏资料为空")
        self._write_cached(cache_path, payload)
        return payload

    def _write_cached(self, relative: str, payload: object) -> None:
        path = self.cache_dir / relative
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

    @staticmethod
    def _download(relative: str) -> object:
        with urlopen(f"{BASE_URL}/{relative}", timeout=5) as response:
            raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("Data Dragon 资料超出大小限制")
        return json.loads(raw)

    @staticmethod
    def _download_community(relative: str) -> object:
        with urlopen(f"{COMMUNITY_URL}/{relative}", timeout=8) as response:
            raw = response.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("CommunityDragon 资料超出大小限制")
        return json.loads(raw)
