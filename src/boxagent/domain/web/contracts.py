"""Provider-independent web evidence; no model or transport dependencies."""

import ipaddress
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from boxagent.core.states import WebReference as WebReference
from boxagent.core.states import WebResearchView as WebResearchView


class WebUnavailable(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WebSource:
    title: str
    url: str
    summary: str
    snippet: str = ""
    published_at: str | None = None
    site_name: str | None = None


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    content: str


class WebProvider(Protocol):
    async def search(self, query: str, *, count: int) -> tuple[WebSource, ...]: ...
    async def fetch(self, url: str) -> WebPage: ...


class WebResearchObserver(Protocol):
    async def __call__(self, view: WebResearchView) -> bool | None: ...


def public_web_url(value: str) -> str:
    """Accept public HTTP(S) evidence links, never local or credential URLs."""
    if not value or len(value) > 8192 or any(ord(char) < 32 for char in value):
        raise ValueError("请输入有效的公开网页链接")
    try:
        parts = urlsplit(value)
        host = parts.hostname or ""
        _ = parts.port
    except ValueError as exc:
        raise ValueError("请输入有效的公开网页链接") from exc
    if (
        parts.scheme not in {"http", "https"}
        or not host
        or parts.username is not None
        or parts.password is not None
    ):
        raise ValueError("仅支持不含账号密码的公开 HTTP/HTTPS 网页链接")
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("仅支持公开网页，不能读取本机或内网地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.isdecimal():
            raise ValueError("仅支持公开网页，不能读取本机或内网地址") from None
    else:
        if not address.is_global:
            raise ValueError("仅支持公开网页，不能读取本机或内网地址")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
