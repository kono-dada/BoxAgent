"""Query the glossary's public client search index, refreshed on use daily."""

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, build_opener

from boxagent.domain.web.contracts import WebSource
from boxagent.infrastructure.web.deepseek import NoRedirect


def index_matches(topic: str, rows: object) -> tuple[WebSource, ...]:
    if not isinstance(rows, list):
        return ()
    exact: list[WebSource] = []
    approximate: list[WebSource] = []
    needle = re.sub(r"\W", "", topic.casefold())
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "meme":
            continue
        title, href, aliases = row.get("title"), row.get("href"), row.get("aliases", [])
        if not isinstance(title, str) or not isinstance(href, str) or not isinstance(aliases, list):
            continue
        if not re.fullmatch(r"/meme/[a-z0-9-]+", href):
            continue
        terms = [
            re.sub(r"\W", "", term.casefold())
            for term in (title, *aliases)
            if isinstance(term, str)
        ]
        source = WebSource(title[:200], urljoin("https://yanfa.lol", href), "")
        if any(needle == term for term in terms):
            exact.append(source)
        elif len(needle) >= 3 and any(
            len(term) == len(needle) and sum(a != b for a, b in zip(term, needle, strict=True)) == 1
            for term in terms
        ):
            approximate.append(source)
    # Ambiguous spelling matches need clarification rather than an arbitrary entry.
    return tuple(exact[:3]) if exact else tuple(approximate) if len(approximate) == 1 else ()


class PublicCultureIndex:
    def __init__(self, cache: Path) -> None:
        self.cache = cache

    async def discover(self, topic: str) -> tuple[WebSource, ...]:
        return await asyncio.to_thread(self._discover, topic)

    def _discover(self, topic: str) -> tuple[WebSource, ...]:
        rows: object = []
        try:
            if self.cache.stat().st_size <= 1024 * 1024:
                rows = json.loads(self.cache.read_text())
                if time.time() - self.cache.stat().st_mtime < 86400:
                    return index_matches(topic, rows)
        except (OSError, ValueError):
            pass
        try:
            request = Request(
                "https://yanfa.lol/api/search-index",
                headers={"User-Agent": "BoxAgent/0.1 (public search index)"},
            )
            with build_opener(NoRedirect()).open(request, timeout=4) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                return index_matches(topic, rows)
            fresh = json.loads(raw)
            if not isinstance(fresh, list):
                return index_matches(topic, rows)
            # Cache discovery fields only; index summaries are not learned evidence.
            rows = [
                {key: row[key] for key in ("title", "href", "aliases", "type") if key in row}
                for row in fresh
                if isinstance(row, dict)
            ]
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache.with_suffix(".tmp")
            temporary.write_text(json.dumps(rows, ensure_ascii=False))
            temporary.replace(self.cache)
        except (OSError, ValueError):
            pass
        return index_matches(topic, rows)
