"""Read allowlisted public glossary articles, without credentials or redirects."""

import asyncio
import re
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from boxagent.domain.web.contracts import WebPage, WebUnavailable, public_web_url
from boxagent.infrastructure.web.deepseek import NoRedirect
from boxagent.infrastructure.web.lol_wiki import ArticleText

PUBLIC_CULTURE_HOSTS = frozenset({"cnmeme.wiki", "yanfa.lol", "www.yehuowiki.com"})


class CultureArticle(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.depth = 0
        self.finished = False
        self.fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if (
            not self.depth
            and not self.finished
            and (
                tag in {"main", "article"}
                or (tag == "div" and "prose" in (attributes.get("class") or "").split())
            )
        ):
            self.depth = 1
            return
        if self.depth:
            self.fragments.append(self.get_starttag_text() or "")
            if tag not in {
                "br",
                "img",
                "hr",
                "input",
                "meta",
                "link",
                "source",
                "wbr",
                "area",
                "base",
                "embed",
                "param",
                "track",
                "col",
            }:
                self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {
            "br",
            "img",
            "hr",
            "input",
            "meta",
            "link",
            "source",
            "wbr",
            "area",
            "base",
            "embed",
            "param",
            "track",
            "col",
        }:
            return
        if self.depth:
            self.depth -= 1
            if not self.depth:
                self.finished = True
            else:
                self.fragments.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.fragments.append(data)

    def handle_entityref(self, name: str) -> None:
        if self.depth:
            self.fragments.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.depth:
            self.fragments.append(f"&#{name};")


class CulturePageReader:
    async def fetch(self, url: str) -> WebPage:
        return await asyncio.to_thread(self._fetch, public_web_url(url))

    def _fetch(self, url: str) -> WebPage:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc not in PUBLIC_CULTURE_HOSTS:
            raise ValueError("社区正文读取只支持已配置的公开 HTTPS 词条站点")
        request = Request(
            url,
            headers={
                "User-Agent": "BoxAgent/0.1 (public glossary research)",
                "Accept": "text/html",
            },
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=5) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            raise WebUnavailable(
                "page_unavailable", f"社区正文暂时无法读取（HTTP {exc.code}）"
            ) from None
        except (URLError, TimeoutError, OSError):
            raise WebUnavailable("network_failed", "社区正文读取失败") from None
        if len(raw) > 1024 * 1024:
            raise WebUnavailable("response_too_large", "社区正文超过读取上限")
        html = raw.decode("utf-8", errors="replace")
        article = CultureArticle()
        article.feed(html)
        parser = ArticleText()
        parser.feed("".join(article.fragments))
        content = parser.text()
        if not content:
            raise WebUnavailable("page_unavailable", "没有取得社区词条正文")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = title_match.group(1).strip() if title_match else parts.path
        return WebPage(url, title[:200], content[:30000])
