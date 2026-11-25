"""Curated esports facts, user preferences, and infrequent conversational references."""

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class CultureEntry:
    id: str
    title: str
    aliases: tuple[str, ...]
    players: tuple[str, ...]
    champions: tuple[str, ...]
    event: str
    summary: str
    usage: str
    avoid: str
    source_url: str
    source_title: str
    source_kind: str
    checked_at: str
    video_offset_seconds: int | None = None
    requires_lol_context: bool = False
    allow_banter: bool = True


@dataclass(frozen=True)
class CultureState:
    enabled: bool = True
    blocked_ids: tuple[str, ...] = ()
    recent_ids: tuple[str, ...] = ()
    last_banter_at: float = 0.0


class CultureRepository(Protocol):
    def load(self) -> CultureState: ...
    def save(self, state: CultureState) -> None: ...


@dataclass(frozen=True)
class CultureLookup:
    status: Literal["found", "not_found", "muted", "cooldown", "recent", "explain_only"]
    entries: tuple[CultureEntry, ...] = ()


def normalize_culture_query(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text.casefold())


class LolCultureService:
    def __init__(
        self,
        entries: tuple[CultureEntry, ...],
        repository: CultureRepository,
        *,
        clock: Callable[[], float] = time.time,
        cooldown: float = 600.0,
    ) -> None:
        self.entries = entries
        self.repository = repository
        self.clock = clock
        self.cooldown = cooldown
        self.lock = asyncio.Lock()

    def question_reference(self, query: str, *, query_id: str, context: str = "") -> str:
        """Ground named factual questions before speech, independently of model tool choice."""
        if not re.search(
            r"是什么|什么梗|什么意思|什么关系|指谁|怎么来|为什么叫|为啥叫|哪年|哪一|哪个|是谁|谁的|知道.+吗|是.+吗|讲讲|解释|说一下|讲一下",
            query,
        ):
            return ""
        if re.search(r"不要|别讲|别说|不喜欢|屏蔽|关闭|恢复|发给|发送|搜索|查一下", query):
            return ""
        needle = normalize_culture_query(query)
        short_context = bool(
            re.search(
                r"英雄联盟|[Ll][Oo][Ll]|电竞|世界赛|[Tt]he[Ss]hy|姜承録|姜承录", query + context
            )
        )
        short_context = short_context or any(
            len(key := normalize_culture_query(term)) >= 4
            and key in normalize_culture_query(context)
            for entry in self.entries
            if not entry.requires_lol_context
            for term in (entry.title, *entry.aliases)
        )
        unrelated = bool(re.search(r"赛恩|塞恩|植物大战|僵尸粉|丧尸|僵尸片|僵尸模式|生化", query))
        matches = [
            entry
            for entry in self.entries
            if any(
                len(key := normalize_culture_query(term))
                >= (2 if entry.requires_lol_context else 4)
                and key in needle
                for term in (entry.title, *entry.aliases)
            )
            and not (entry.requires_lol_context and unrelated)
        ]
        payload: dict[str, str | list[str]]
        if matches and all(entry.requires_lol_context for entry in matches) and not short_context:
            payload = {
                "query_id": query_id,
                "id": "needs_context",
                "answer": "你问的是英雄联盟社区里的这个称呼，还是其他语境的意思？",
            }
        elif 1 < len(matches) <= 3 and all(entry.requires_lol_context for entry in matches):
            payload = {
                "query_id": query_id,
                "id": ",".join(entry.id for entry in matches),
                "answer": "".join(entry.summary for entry in matches),
                "source_urls": [entry.source_url for entry in matches],
            }
        elif len(matches) == 1:
            entry = matches[0]
            # No model-generated connective facts: read only the checked event and summary.
            payload = {
                "query_id": query_id,
                "id": entry.id,
                "answer": f"{entry.event}。{entry.summary}",
                "source_url": entry.source_url,
                "avoid": entry.avoid,
            }
        else:
            esports_context = any(
                len(key := normalize_culture_query(entry.title)) >= 4
                and key in normalize_culture_query(context)
                for entry in self.entries
            )
            explicit_esports = re.search(r"英雄联盟|世界赛|全球总决赛|职业选手|电竞", query)
            if not re.search(r"名场面|什么梗|降临", query) or not (
                esports_context or explicit_esports
            ):
                return ""
            payload = {
                "query_id": query_id,
                "id": "unverified",
                "answer": (
                    '这个名场面我还没有可靠资料，暂时不能确认是哪位选手、哪场比赛'
                    '。你能补充一下选手或对阵吗？'
                ),
            }
        return (
            "<boxagent_culture_reference>"
            + json.dumps(payload, ensure_ascii=False)
            + "</boxagent_culture_reference>"
        )

    async def lookup(self, query: str, *, purpose: Literal["explain", "banter"]) -> CultureLookup:
        async with self.lock:
            state = self.repository.load()
            now = self.clock()
            if purpose == "banter":
                if not state.enabled:
                    return CultureLookup("muted")
                if now < state.last_banter_at + self.cooldown and state.last_banter_at > 0:
                    return CultureLookup("cooldown")
            needle = normalize_culture_query(query)
            if not needle:
                return CultureLookup("not_found")
            overview = needle in {"经典梗", "名场面", "经典名场面"}
            ranked: list[tuple[int, CultureEntry]] = []
            specific: list[tuple[int, CultureEntry]] = []
            for entry in self.entries:
                if entry.requires_lol_context and re.search(
                    r"赛恩|塞恩|植物大战|僵尸粉|丧尸|僵尸片|僵尸模式|生化", query
                ):
                    continue
                terms = (entry.title, *entry.aliases, *entry.players, *entry.champions)
                score = sum(
                    1
                    for term in terms
                    if (key := normalize_culture_query(term)) and (key in needle or needle in key)
                )
                if overview or score:
                    ranked.append((score, entry))
                keys = [normalize_culture_query(term) for term in (entry.title, *entry.aliases)]
                minimum = 2 if entry.requires_lol_context else 4
                exact = max(
                    (len(key) for key in keys if len(key) >= minimum and key in needle), default=0
                )
                if exact:
                    specific.append((exact, entry))
            if specific:
                ranked = specific
            ranked.sort(key=lambda item: (-item[0], item[1].id))
            if not ranked:
                return CultureLookup("not_found")
            if purpose == "explain":
                return CultureLookup("found", tuple(entry for _, entry in ranked[:3]))
            if all(not entry.allow_banter for _, entry in ranked):
                return CultureLookup("explain_only")
            available = [
                entry
                for _, entry in ranked
                if entry.allow_banter
                and entry.id not in state.blocked_ids
                and entry.id not in state.recent_ids
            ]
            if not available:
                return CultureLookup("recent")
            selected = available[0]
            # Reserve at retrieval time so retries and concurrent calls cannot repeat a reference.
            self.repository.save(
                CultureState(
                    state.enabled, state.blocked_ids, (*state.recent_ids, selected.id)[-5:], now
                )
            )
            return CultureLookup("found", (selected,))

    async def set_preference(self, *, enabled: bool, name: str = "") -> str | None:
        async with self.lock:
            state = self.repository.load()
            if not name:
                self.repository.save(
                    CultureState(enabled, state.blocked_ids, state.recent_ids, state.last_banter_at)
                )
                return "全部主动接梗"
            needle = normalize_culture_query(name)
            matches = [
                entry
                for entry in self.entries
                if needle
                in {
                    normalize_culture_query(value)
                    for value in (entry.id, entry.title, *entry.aliases)
                }
            ]
            if len(matches) != 1:
                return None
            entry = matches[0]
            blocked = set(state.blocked_ids)
            if enabled:
                blocked.discard(entry.id)
            else:
                blocked.add(entry.id)
            self.repository.save(
                CultureState(
                    state.enabled, tuple(sorted(blocked)), state.recent_ids, state.last_banter_at
                )
            )
            return entry.title
