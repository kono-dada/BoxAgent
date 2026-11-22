"""DeepSeek's documented standalone Search/Fetch APIs, using the existing key."""

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Protocol
from urllib.parse import urlsplit

from boxagent.domain.web.contracts import WebPage, WebSource, WebUnavailable, public_web_url


class JsonPoster(Protocol):
    def __call__(self, url: str, payload: Mapping[str, object], *,
                 api_key: str, timeout: float) -> object: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: urllib.request.Request, fp: object, code: int,
                         msg: str, headers: object, newurl: str) -> None:
        # A provider redirect must never forward the API key to another host.
        return None


HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("invalid_request", "搜索请求格式不正确，请调整关键词或链接"),
    401: ("authentication_failed", "DeepSeek Key 无效，请检查模型服务配置"),
    402: ("insufficient_balance", "DeepSeek 余额不足，暂时无法联网检索"),
    403: ("fetch_denied", "该网页无法通过搜索服务的访问策略检查"),
    404: ("page_unavailable", "暂时无法读取该网页，可能是链接失效或站点限制"),
    408: ("timeout", "联网检索超时，请稍后重试"),
    429: ("rate_limited", "联网检索暂时达到限速，请稍后重试"),
    504: ("timeout", "联网检索超时，请稍后重试"),
}


def post_json(url: str, payload: Mapping[str, object], *,
              api_key: str, timeout: float) -> object:
    request = urllib.request.Request(url, json.dumps(dict(payload)).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        code, message = HTTP_ERRORS.get(exc.code, ("service_unavailable", "联网检索服务暂不可用"))
        raise WebUnavailable(code, message) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise WebUnavailable("network_failed", "暂时无法连接联网检索服务，请检查网络后重试") from None
    if len(raw) > 2 * 1024 * 1024:
        raise WebUnavailable("response_too_large", "网页内容超过本次读取上限")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise WebUnavailable("invalid_response", "联网检索未返回有效数据") from None


def object_record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise WebUnavailable("invalid_response", "联网检索未返回有效数据")
    return dict(value)


def text_field(record: Mapping[str, object], name: str, *, optional: bool = False) -> str:
    value = record.get(name)
    if isinstance(value, str):
        return value
    if value is None and optional:
        return ""
    raise WebUnavailable("invalid_response", "联网检索未返回有效数据")


class DeepSeekWebProvider:
    def __init__(self, *, api_key: str, base_url: str = "https://api.deepseek.com",
                 timeout: float = 20, poster: JsonPoster = post_json) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("DeepSeek 搜索服务地址必须是 HTTPS，且不含账号密码或查询参数")
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.api_key = api_key
        self.timeout = timeout
        self.poster = poster

    async def _request(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        if not self.api_key:
            raise WebUnavailable("not_configured", "请先填写 DeepSeek API Key，再使用联网检索")
        try:
            value = await asyncio.to_thread(self.poster, self.base_url + path, payload,
                                           api_key=self.api_key, timeout=self.timeout)
        except WebUnavailable as exc:
            if path.endswith("/search") and exc.code == "page_unavailable":
                raise WebUnavailable("capability_unavailable", "当前服务地址不支持搜索接口，请使用 DeepSeek 官方地址或支持工具 API 的网关") from None
            raise
        return object_record(value)

    async def search(self, query: str, *, count: int = 5) -> tuple[WebSource, ...]:
        if not query.strip() or len(query) > 128 or not 1 <= count <= 5:
            raise ValueError("搜索词须为 1–128 字，结果条数须为 1–5")
        record = await self._request("/v1/tools/search", {
            "query": query, "count": count, "need_content": False})
        results = record.get("results")
        if not isinstance(results, list):
            raise WebUnavailable("invalid_response", "联网检索未返回有效数据")
        sources: list[WebSource] = []
        seen: set[str] = set()
        for item in results:
            row = object_record(item)
            try:
                url = public_web_url(text_field(row, "url"))
            except ValueError:
                continue
            if url in seen:
                continue
            seen.add(url)
            sources.append(WebSource(
                title=text_field(row, "title"), url=url,
                summary=text_field(row, "summary", optional=True),
                snippet=text_field(row, "snippet", optional=True),
                published_at=text_field(row, "date", optional=True) or None,
                site_name=text_field(row, "site_name", optional=True) or None))
            if len(sources) == count:
                break
        return tuple(sources)

    async def fetch(self, url: str) -> WebPage:
        url = public_web_url(url)
        record = await self._request("/v1/tools/fetch", {"url": url})
        content = text_field(record, "content")
        if not content.strip():
            raise WebUnavailable("page_unavailable", "该网页没有可读取的正文")
        return WebPage(url=url, title=text_field(record, "title", optional=True), content=content)
