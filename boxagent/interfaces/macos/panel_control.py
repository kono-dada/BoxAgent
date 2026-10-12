"""Local panel commands queued onto the native host's main thread."""
from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import socketserver
import threading
from pathlib import Path
from typing import Any

from boxagent.agent.harness.persona import persona_name, update_persona_name
from boxagent.interfaces.macos.run_records import SessionRunRecords
from boxagent.interfaces.macos.pets.vrm import AssetServer
from boxagent.interfaces.macos.pets.size import PET_MIN_SCALE, PET_MAX_SCALE, valid_pet_scale


class PanelControl:
    """Socket threads only transport JSON; AppKit work runs in ``pump``."""

    def __init__(self, owner: Any, path: Path) -> None:
        self.owner = owner  # Dynamic PyObjC boundary.
        self.path = path
        self.pending: queue.SimpleQueue[tuple[str, dict, concurrent.futures.Future]] = queue.SimpleQueue()
        self.preview: AssetServer | None = None
        self.preview_directory: Path | None = None
        self.closed = False
        control = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                while line := self.rfile.readline(1024 * 1024):
                    request: dict = {}
                    try:
                        request = json.loads(line)
                        if not isinstance(request, dict):
                            request = {}
                        if request.get("version") != 1 or request.get("type") != "request":
                            raise ValueError("桌面控制请求格式无效")
                        future: concurrent.futures.Future = concurrent.futures.Future()
                        control.pending.put((str(request["method"]), request.get("params") or {}, future))
                        try:
                            result = future.result(timeout=45)
                        except TimeoutError:
                            future.cancel()
                            raise
                        response = {"id": request["id"], "ok": True, "result": result}
                    except Exception as exc:
                        response = {"id": request.get("id", ""), "ok": False, "error": str(exc)}
                    try:
                        self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
                    except (BrokenPipeError, ConnectionError):
                        return

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
            block_on_close = False

        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        self.server = Server(str(path), Handler)
        os.chmod(path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def pump(self) -> None:
        while not self.pending.empty():
            method, params, future = self.pending.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(self.dispatch(method, params))
            except Exception as exc:
                future.set_exception(exc)

    def dispatch(self, method: str, params: dict) -> dict:
        owner = self.owner
        if method == "desktop_snapshot":
            return {"name": owner.agent_name, "bubble_open": owner.bubble_open,
                    "appearance": str(owner.appearance.directory),
                    "appearance_ready": owner.appearance.ready,
                    "appearance_error": owner.appearance.error,
                    "switching": getattr(owner, "pending_appearance", None) is not None,
                    "feedback": owner.input_feedback,
                    "scale": owner.pet_scale, "min_scale": PET_MIN_SCALE, "max_scale": PET_MAX_SCALE}
        if method == "desktop_set_pet_scale":
            scale = params.get("scale")
            if not valid_pet_scale(scale):
                raise ValueError("桌宠大小必须在 50%～600% 之间")
            owner.setPetScale(float(scale))
            return self.dispatch("desktop_snapshot", {})
        if method == "desktop_toggle_bubble":
            owner.toggleBubble_(None)
            return self.dispatch("desktop_snapshot", {})
        if method == "desktop_avatars":
            return {"items": [{"id": str(path.parent),
                               "name": owner.pet_catalog.read_json(path).get("displayName", path.parent.name),
                               "description": owner.pet_catalog.read_json(path).get("description", "")}
                              for path in owner.pet_catalog.vrm_manifests()],
                    **self.dispatch("desktop_snapshot", {})}
        if method in {"desktop_preview_avatar", "desktop_select_avatar"}:
            manifests = {str(path.parent): path for path in owner.pet_catalog.vrm_manifests()}
            identity = str(params.get("id", ""))
            if identity not in manifests:
                raise ValueError("请选择目录中的有效 3D 形象")
            directory = manifests[identity].parent
            if method == "desktop_preview_avatar":
                if self.preview_directory != directory:
                    if self.preview:
                        self.preview.close()
                    self.preview = AssetServer(directory, owner.pet_catalog.read_json(manifests[identity]))
                    self.preview_directory = directory
                return {"url": self.preview.url if self.preview else ""}
            if getattr(owner, "pending_appearance", None) is not None:
                raise RuntimeError("形象仍在加载，请稍后重试")

            class Selection:
                def representedObject(self) -> str:
                    return identity

            owner.useLocalVrm_(Selection())
            return self.dispatch("desktop_snapshot", {})
        if method == "desktop_persona":
            source = owner.editable_soul_file if owner.editable_soul_file.is_file() else owner.soul_file
            content = source.read_text(encoding="utf-8")
            return {"name": persona_name(content), "content": content, "path": str(source)}
        if method == "desktop_save_persona":
            content = update_persona_name(str(params.get("content", "")), str(params.get("name", "")))
            target = owner.editable_soul_file
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".panel.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
            owner.soul_file = target
            owner.reloadPersona()
            return self.dispatch("desktop_persona", {})
        if method == "desktop_runs":
            records = SessionRunRecords(owner.data_dir / "runs")
            session_id = str(params.get("session_id", ""))
            rows = []
            for directory in reversed(records.directories(session_id)):
                manifest = json.loads((directory / "manifest.json").read_text())
                rows.append({"id": str(directory), "manifest": manifest,
                             "files": [p.name for p in directory.iterdir() if p.is_file()]})
            return {"items": rows}
        if method == "desktop_open_run":
            session_id = str(params.get("session_id", ""))
            records = SessionRunRecords(owner.data_dir / "runs")
            identity = params.get("id")
            if identity:
                directory = next((p for p in records.directories(session_id) if str(p) == identity), None)
                if directory is None:
                    raise ValueError("运行记录不属于此会话")
            else:
                directory = records.latest(session_id) if params.get("latest") else None
                directory = directory or records.materialize_view(session_id)
            owner._openDirectory(directory)
            return {"status": "succeeded"}
        if method == "desktop_open_logs":
            owner.openDiagnosticLogs_(None)
            return {"status": "succeeded"}
        if method == "desktop_quit":
            # Allow the transport to acknowledge before terminating AppKit.
            from PyObjCTools import AppHelper
            AppHelper.callLater(.3, owner.quit_, None)
            return {"status": "stopping"}
        raise ValueError(f"未知桌面控制方法：{method}")

    def close(self) -> None:
        self.closed = True
        self.server.shutdown()
        self.server.server_close()
        self.path.unlink(missing_ok=True)
        if self.preview:
            self.preview.close()
        while not self.pending.empty():
            _method, _params, future = self.pending.get()
            if not future.done():
                future.set_exception(ConnectionError("桌宠已关闭"))
