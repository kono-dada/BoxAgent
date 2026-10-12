"""本地 VRM 资源服务：Electron 渲染器通过受限回环 HTTP 加载。"""

import json
import mimetypes
import secrets
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


WEB_ROOT = Path(__file__).resolve().parents[4] / "assets/vrm"


def resource_files(directory, manifest):
    """共享动作只能来自仓库中具名的动作包，其他资源仍限于形象目录。"""
    directory = Path(directory).resolve()
    actions = manifest.get("actions", {})
    if not isinstance(actions, dict):
        raise ValueError("VRM 动作清单必须为对象")
    pack = manifest.get("motionPack")
    if pack is not None and (not isinstance(pack, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", pack)):
        raise ValueError("共享动作包标识无效")
    pack_root = WEB_ROOT / "motions" / pack if pack else None
    resources = [(manifest["model"], directory, manifest["model"])]
    for value in actions.values():
        if pack_root is not None:
            if not isinstance(value, str) or not value.startswith("motions/"):
                raise ValueError("共享动作必须使用 motions/ 虚拟路径")
            resources.append((value, pack_root, value.removeprefix("motions/")))
        else:
            resources.append((value, directory, value))
    files = {"pet.json": directory / "pet.json"}
    for name, root, value in resources:
        if not isinstance(value, str):
            raise ValueError("VRM 资源路径必须是字符串")
        path = root / value
        if (root.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                or not path.is_file() or path.is_symlink()):
            raise ValueError("VRM 资源不存在或超出资源目录")
        with path.open("rb") as stream:
            if stream.read(43).startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise ValueError("VRM 资源尚未下载，请运行 git lfs pull")
        files[name] = path
    return files


def read_manifest(directory):
    """验证清单和实际资源，拒绝目录逃逸及未下载的 LFS 指针。"""
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "pet.json").read_text())
    if manifest.get("type") != "vrm" or manifest.get("version") != 1:
        raise ValueError("VRM 形象清单版本无效")
    resource_files(directory, manifest)
    return manifest


class AssetServer:
    """随机令牌和精确文件白名单，仅监听本机回环地址。"""

    def __init__(self, directory, manifest):
        self.token = secrets.token_urlsafe(24)
        files = {"index.html": WEB_ROOT / "index.html", "viewer.js": WEB_ROOT / "viewer.js"}
        for name, path in resource_files(directory, manifest).items():
            files[f"asset/{name}"] = path
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                route = unquote(urlsplit(self.path).path)
                prefix = f"/{token}/"
                path = files.get(route[len(prefix):]) if route.startswith(prefix) else None
                if path is None:
                    self.send_error(404)
                    return
                try:
                    data = path.read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self' blob:")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.http.server_port}/{token}/index.html"

    def close(self):
        self.http.shutdown()
        self.http.server_close()
