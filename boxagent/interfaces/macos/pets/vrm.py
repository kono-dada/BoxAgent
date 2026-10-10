"""本地 VRM 渲染器：原生宿主管交互，WebKit 只管角色画面。"""

import json
import mimetypes
import secrets
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from boxagent.core.states import presentation_state

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
        if pack:
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


class VrmAppearance:
    size = (252.0, 182.0)

    @staticmethod
    def prepare(directory):
        manifest = read_manifest(directory)
        if not (WEB_ROOT / "viewer.js").is_file():
            raise ValueError("请先运行 pnpm --dir web/vrm install 和 pnpm --dir web/vrm build")
        return manifest

    def __init__(self, directory, prepared=None):
        import AppKit as AK
        import objc
        from Foundation import NSObject, NSURL, NSURLRequest
        from WebKit import WKWebView, WKWebViewConfiguration, WKWebsiteDataStore

        self.directory = Path(directory).resolve()
        self.manifest = prepared or self.prepare(self.directory)
        self.ready = False
        self.error = None
        self.closed = False
        self.started = time.monotonic()
        self.last_payload = None
        self.last_sent = 0
        self.dragging = False
        self.server = AssetServer(self.directory, self.manifest)

        # PyObjC 类必须只注册一次；实例通过 owner 路由消息。
        global _MessageHandler
        if "_MessageHandler" not in globals():
            class VrmMessageHandler(NSObject):
                owner = objc.ivar()

                def userContentController_didReceiveScriptMessage_(handler, _controller, message):
                    owner = handler.owner
                    if owner is None or owner.closed:
                        return
                    body = message.body()
                    if not hasattr(body, "get"):
                        return
                    kind = body.get("type")
                    if kind == "ready" and not owner.error:
                        owner.ready = True
                        owner.web.setHidden_(False)
                    elif kind == "error":
                        owner.fail(str(body.get("message", "渲染失败")))
                    elif kind == "warning":
                        print(f"VRM 动作提示：{body.get('message')}", flush=True)

                def webViewWebContentProcessDidTerminate_(handler, _web):
                    if handler.owner:
                        handler.owner.fail("WebKit 渲染进程已退出")

            _MessageHandler = VrmMessageHandler
        self.view = AK.NSView.alloc().initWithFrame_(((0, 0), self.size))
        config = WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(WKWebsiteDataStore.nonPersistentDataStore())
        self.handler = _MessageHandler.alloc().init()
        self.handler.owner = self
        config.userContentController().addScriptMessageHandler_name_(self.handler, "pet")
        self.web = WKWebView.alloc().initWithFrame_configuration_(((0, 0), self.size), config)
        self.web.setValue_forKey_(False, "drawsBackground")
        self.web.setNavigationDelegate_(self.handler)
        self.web.setHidden_(True)
        self.view.addSubview_(self.web)
        self.web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(self.server.url)))

    def fail(self, message):
        if self.closed or self.error:
            return
        self.error = message
        self.ready = False
        self.web.setHidden_(True)
        print(f"3D 形象加载失败：{message}", flush=True)

    def present(self, snapshot, now, pointer=None):
        if self.closed:
            return
        if not self.ready:
            if not self.error and now - self.started > 30:
                self.fail("模型加载超时")
            return
        state = presentation_state(snapshot)
        payload = {"state": state, "pointer": [round(v / 2) * 2 for v in (pointer or (0, 0))]}
        if payload != self.last_payload and now - self.last_sent > 0.08:
            self.last_payload, self.last_sent = payload, now
            self.web.evaluateJavaScript_completionHandler_(f"window.pet.present({json.dumps(payload)})", None)

    def interact(self, kind, velocity=(0, 0)):
        """鼠标事件直接传入渲染进程，不能等待业务状态或动画轮询。"""
        if self.closed:
            return
        if kind in {"drag-start", "drag-move"}:
            self.dragging = True
        elif kind == "drag-end":
            self.dragging = False
        if self.ready:
            payload = {"kind": kind, "velocity": list(velocity)}
            self.web.evaluateJavaScript_completionHandler_(f"window.pet.interact({json.dumps(payload)})", None)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.handler.owner = None
        self.web.evaluateJavaScript_completionHandler_("window.pet && window.pet.dispose()", None)
        self.web.configuration().userContentController().removeScriptMessageHandlerForName_("pet")
        self.web.setNavigationDelegate_(None)
        self.web.stopLoading()
        self.web.loadHTMLString_baseURL_("", None)
        self.server.close()
