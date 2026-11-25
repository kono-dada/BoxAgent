"""Bailian search metadata and direct LoL Wiki article reads."""

import asyncio
from urllib.parse import urlsplit

from boxagent.domain.web.contracts import (
    WebPage,
    WebProvider,
    WebSource,
    WebUnavailable,
    public_web_url,
)
from boxagent.infrastructure.web.culture_pages import PUBLIC_CULTURE_HOSTS, CulturePageReader
from boxagent.infrastructure.web.deepseek import JsonPoster, object_record, post_json, text_field
from boxagent.infrastructure.web.lol_wiki import WIKI_HOST, LolWikiReader


class BailianWebProvider:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        model: str = "qwen-plus",
        fallback: WebProvider | None = None,
        timeout: float = 18,
        poster: JsonPoster = post_json,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.fallback = fallback
        self.timeout = timeout
        self.poster = poster
        self.wiki = LolWikiReader()
        self.culture = CulturePageReader()

    async def search(self, query: str, *, count: int = 5) -> tuple[WebSource, ...]:
        if not query.strip() or len(query) > 128 or not 1 <= count <= 5:
            raise ValueError("搜索词须为 1–128 字，结果条数须为 1–5")
        if not self.api_key:
            raise WebUnavailable("not_configured", "请先配置百炼 API Key")
        try:
            payload = await asyncio.to_thread(
                self.poster,
                self.endpoint,
                {
                    "model": self.model,
                    "input": {"messages": [{"role": "user", "content": query}]},
                    "parameters": {
                        "enable_search": True,
                        "enable_thinking": False,
                        "max_tokens": 100,
                        "result_format": "message",
                        "search_options": {"forced_search": True, "enable_source": True},
                    },
                },
                api_key=self.api_key,
                timeout=self.timeout,
            )
        except WebUnavailable as exc:
            raise WebUnavailable(exc.code, str(exc).replace("DeepSeek", "百炼")) from None
        output = object_record(object_record(payload).get("output"))
        info = output.get("search_info")
        if not isinstance(info, dict) or not isinstance(info.get("search_results"), list):
            # Model-generated answers and links are never treated as search evidence.
            raise WebUnavailable("capability_unavailable", "百炼没有返回搜索来源，无法核验这次检索")
        sources: list[WebSource] = []
        seen: set[str] = set()
        for value in info["search_results"]:
            row = object_record(value)
            try:
                url = public_web_url(text_field(row, "url"))
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            sources.append(
                WebSource(
                    title=text_field(row, "title"),
                    url=url,
                    summary=text_field(row, "summary", optional=True),
                    snippet=text_field(row, "snippet", optional=True),
                    site_name=urlsplit(url).hostname,
                )
            )
            if len(sources) >= count:
                break
        return tuple(sources)

    async def fetch(self, url: str) -> WebPage:
        url = public_web_url(url)
        if urlsplit(url).hostname == WIKI_HOST:
            return await self.wiki.fetch(url)
        if urlsplit(url).hostname in PUBLIC_CULTURE_HOSTS:
            return await self.culture.fetch(url)
        if self.fallback is not None:
            return await self.fallback.fetch(url)
        raise WebUnavailable("capability_unavailable", "当前百炼读取入口仅支持 LoL Wiki 词条正文")
