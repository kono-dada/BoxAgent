"""商店专用下载；普通 TLS 失败时复用旧项目的公开 DNS 恢复策略。"""

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE = "https://codex-pets.net"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("商店资源不允许重定向")


def read_limited(response, limit):
    if int(response.headers.get("Content-Length", "0")) > limit:
        raise ValueError("商店响应过大")
    chunks, size, deadline = [], 0, time.monotonic() + 15
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("商店连接超时")
        chunk = response.read(min(65536, limit + 1 - size))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        size += len(chunk)
        if size > limit:
            raise ValueError("商店响应过大")


class PetTransport:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect())
        self.addresses = []
        self.resolved_until = 0
        self.addresses_until = 0
        self.dns_lock = threading.Lock()

    def __call__(self, url, limit):
        target = urllib.parse.urlsplit(url)
        if target.scheme != "https" or target.netloc != "codex-pets.net":
            raise ValueError("只允许访问形象商店")
        if self.resolved_until > time.monotonic():
            try:
                return self.resolved(url, limit)
            except (OSError, ValueError, http.client.HTTPException):
                self.resolved_until = 0
        try:
            with self.opener.open(url, timeout=12) as response:
                return read_limited(response, limit)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"商店返回 HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, ssl.SSLError):
            result = self.resolved(url, limit)
            self.resolved_until = time.monotonic() + 300
            return result

    def resolved(self, url, limit):
        with self.dns_lock:
            self.resolve_addresses()
            address = self.addresses[0]
        target = urllib.parse.urlsplit(url)
        connection = http.client.HTTPSConnection(target.hostname, timeout=12)
        try:
            # 显式连接公开地址，证书和 SNI 仍使用原域名；不关闭 TLS 校验。
            sock = socket.create_connection((address, 443), timeout=12)
            try:
                connection.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=target.hostname)
            except BaseException:
                sock.close()
                raise
            connection.request("GET", urllib.parse.urlunsplit(("", "", target.path, target.query, "")),
                               headers={"Accept": "*/*"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"商店返回 HTTP {response.status}")
            return read_limited(response, limit)
        finally:
            connection.close()

    def resolve_addresses(self):
        if not self.addresses or self.addresses_until <= time.monotonic():
            request = urllib.request.Request(
                "https://cloudflare-dns.com/dns-query?name=codex-pets.net&type=A",
                headers={"Accept": "application/dns-json"})
            with self.opener.open(request, timeout=6) as response:
                data = json.loads(read_limited(response, 65536))
            self.addresses = [item["data"] for item in data.get("Answer", [])
                              if item.get("type") == 1 and ipaddress.ip_address(item["data"]).is_global]
            self.addresses_until = time.monotonic() + 300
        if not self.addresses:
            raise ValueError("形象商店没有可用的公开地址")
