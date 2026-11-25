"""Read public LoL Wiki articles through MediaWiki's parse API."""

import asyncio
import json
import re
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import Request, build_opener

from boxagent.domain.web.contracts import WebPage, WebUnavailable, public_web_url
from boxagent.infrastructure.web.deepseek import NoRedirect

WIKI_HOST = "wiki.leagueoflegends.com"
_MAX_BYTES = 2 * 1024 * 1024


class ArticleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        elif not self.hidden and tag in {"p", "div", "li", "br", "tr", "h2", "h3", "h4"}:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()
        elif not self.hidden and tag in {"p", "div", "li", "tr", "h2", "h3", "h4"}:
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.chunks.append(data)

    def text(self) -> str:
        return "\n".join(
            line for raw in "".join(self.chunks).splitlines() if (line := " ".join(raw.split()))
        )


class LolWikiReader:
    def __init__(self, *, timeout: float = 12) -> None:
        self.timeout = timeout

    async def fetch(self, url: str) -> WebPage:
        return await asyncio.to_thread(self._fetch, public_web_url(url))

    def _fetch(self, url: str) -> WebPage:
        parts = urlsplit(url)
        segments = parts.path.strip("/").split("/", 1)
        if (
            parts.scheme != "https"
            or parts.netloc != WIKI_HOST
            or len(segments) != 2
            or not re.fullmatch(r"[a-z]{2}-[a-z]{2}", segments[0])
        ):
            raise ValueError("请输入 LoL Wiki 的 HTTPS 词条链接")
        locale, article = segments
        title = unquote(article)
        if not title or title in {"api.php", "index.php"}:
            raise ValueError("请输入具体的 Wiki 词条")
        api = f"https://{WIKI_HOST}/{locale}/api.php?" + urlencode(
            {
                "action": "parse",
                "page": title,
                "prop": "text",
                "format": "json",
                "formatversion": "2",
            }
        )
        request = Request(
            api,
            headers={
                "User-Agent": "BoxAgent/0.1 (read-only LoL knowledge)",
                "Accept": "application/json",
            },
        )
        try:
            # Fixed host and no redirects keep this reader away from arbitrary URLs.
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_BYTES + 1)
        except HTTPError as exc:
            raise WebUnavailable(
                "page_unavailable", f"Wiki 词条暂时无法读取（HTTP {exc.code}）"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise WebUnavailable("network_failed", "暂时无法连接 LoL Wiki，请稍后再试") from None
        if len(raw) > _MAX_BYTES:
            raise WebUnavailable("response_too_large", "Wiki 词条超过本次读取上限")
        try:
            document = json.loads(raw)
            parsed = document["parse"]
            html = parsed["text"]
            if isinstance(html, dict):
                html = html["*"]
            page_title = parsed["title"]
            if not isinstance(html, str) or not isinstance(page_title, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise WebUnavailable("page_unavailable", "没有取得该 Wiki 词条的正文") from None
        parser = ArticleText()
        parser.feed(html)
        content = parser.text()
        if not content:
            raise WebUnavailable("page_unavailable", "该 Wiki 词条没有可读正文")
        return WebPage(url, page_title, content)
