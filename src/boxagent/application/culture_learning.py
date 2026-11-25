"""Automatically retrieve and reuse attributed notes for unfamiliar LoL terms."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from boxagent.domain.proactivity.learned_culture import (
    CultureDiscovery,
    CultureEvidence,
    LearnedCultureNote,
    LearnedCultureRepository,
    learning_topic,
)
from boxagent.domain.web.contracts import WebPage, WebProvider, WebSource, WebUnavailable

logger = logging.getLogger(__name__)
READABLE_CULTURE_SITES = ("cnmeme.wiki", "yanfa.lol", "www.yehuowiki.com")


def evidence_excerpt(topic: str, page: WebPage) -> str:
    lines = [line.strip() for line in page.content.splitlines() if line.strip()]
    candidates: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if topic.casefold() not in line.casefold() or len(line) < 15:
            continue
        if re.search(r"忽略.*指令|API.?KEY|system prompt|执行命令|密码", line, re.I):
            continue
        # Include a preceding definition when a short heading and the next
        # paragraph distinguish an esports nickname from a game character.
        if index and len(line) < 100 and re.search(r"外号|选手|社区|俗称|粉丝", lines[index - 1]):
            line = lines[index - 1] + " " + line
        score = sum(word in line for word in ("外号", "选手", "社区", "称呼", "粉丝"))
        candidates.append((score, line[:180]))
    return max(candidates, key=lambda item: item[0])[1] if candidates else ""


class CultureLearningService:
    def __init__(
        self,
        provider: WebProvider | None,
        repository: LearnedCultureRepository,
        *,
        clock: Callable[[], float] = time.time,
        ttl: float = 7 * 86400,
        discovery: CultureDiscovery | None = None,
        timeout: float = 12,
    ) -> None:
        self.provider = provider
        self.repository = repository
        self.clock, self.ttl, self.timeout = clock, ttl, timeout
        self.lock = asyncio.Lock()
        self.discovery = discovery

    async def reference(self, query: str, *, query_id: str, context: str = "") -> str:
        topic = learning_topic(query, context)
        if not topic:
            return ""
        try:
            async with asyncio.timeout(self.timeout):
                async with self.lock:
                    note = self.repository.load(topic)
                    if note is not None and self.clock() < note.expires_at:
                        return note.reference(query_id)
                    if self.provider is not None:
                        learned = await self._learn(topic, query_id)
                        if learned:
                            return learned
        except (TimeoutError, WebUnavailable, ValueError, OSError) as exc:
            logger.info("culture research unavailable: %s", type(exc).__name__)
        payload = {
            "query_id": query_id,
            "id": "learning_unverified",
            "answer": "这个称呼我还没查到能核对的正文，暂时不能确认。你可以补充选手或原视频出处。",
        }
        return (
            "<boxagent_culture_reference>"
            + json.dumps(payload, ensure_ascii=False)
            + "</boxagent_culture_reference>"
        )

    async def _learn(self, topic: str, query_id: str) -> str:
        provider = self.provider
        assert provider is not None
        evidence: list[CultureEvidence] = []
        seen: set[str] = set()

        async def read(sources: tuple[WebSource, ...]) -> None:
            for source in sources:
                host = urlsplit(source.url).hostname or ""
                if host not in READABLE_CULTURE_SITES or host in seen:
                    continue
                try:
                    page = await provider.fetch(source.url)
                except (WebUnavailable, ValueError, OSError):
                    continue
                excerpt = evidence_excerpt(topic, page)
                if not excerpt and self.discovery is not None:
                    excerpt = evidence_excerpt(source.title, page)
                if excerpt:
                    evidence.append(CultureEvidence(page.url, page.title[:200], excerpt))
                    seen.add(host)
                if len(evidence) >= 2:
                    break

        if self.discovery is not None:
            await read(await self.discovery.discover(topic))
            # A readable indexed definition can be retained as a clearly
            # attributed single-source note; do not stall speech chasing consensus.
            if evidence:
                now = self.clock()
                note = LearnedCultureNote(topic, tuple(evidence), now, now + self.ttl)
                self.repository.save(note)
                return note.reference(query_id)
        for suffix in (" " + " OR ".join("site:" + site for site in READABLE_CULTURE_SITES), ""):
            sources = await provider.search(
                f"英雄联盟 {topic} 是什么梗 外号 来源" + suffix, count=5
            )
            await read(sources)
            if len(evidence) >= 2:
                break
        if not evidence:
            return ""
        now = self.clock()
        note = LearnedCultureNote(topic, tuple(evidence), now, now + self.ttl)
        self.repository.save(note)
        return note.reference(query_id)
