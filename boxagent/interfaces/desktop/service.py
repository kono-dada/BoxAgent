"""Local resources and Engine supervision; Electron owns every visible window."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Protocol, TypedDict

from boxagent.agent.harness.persona import load_persona, persona_name, update_persona_name
from boxagent.interfaces.desktop.pets.catalog import PetCatalog
from boxagent.interfaces.desktop.pets.size import load_pet_scale, save_pet_scale, valid_pet_scale
from boxagent.interfaces.desktop.pets.vrm import AssetServer, read_manifest
from boxagent.interfaces.desktop.run_records import SessionRunRecords

logger = logging.getLogger(__name__)


class Backend(Protocol):
    def close(self) -> None: ...


class PreparedAvatar(TypedDict):
    id: str
    token: str
    url: str


class DesktopService:
    def __init__(self, *, backend: Backend, catalog: PetCatalog, data_dir: Path,
                 log_dir: Path, soul_file: Path, directory: Path) -> None:
        self.backend = backend
        self.catalog = catalog
        self.data_dir = data_dir
        self.log_dir = log_dir
        self.soul_file = soul_file
        default_soul = Path(__file__).resolve().parents[3] / "assets/personas/default/SOUL.md"
        self.editable_soul = data_dir / "SOUL.md" if soul_file == default_soul else soul_file
        self.directory = directory
        self.scale = load_pet_scale(data_dir)
        self.servers: dict[str, AssetServer] = {}
        self.prepared: dict[str, Path] = {}
        self.preview_directory: Path | None = None
        self.closed = False
        self.lock = threading.RLock()

    def snapshot(self) -> dict[str, object]:
        source = self.editable_soul if self.editable_soul.is_file() else self.soul_file
        return {"name": load_persona(source).name, "appearance": str(self.directory),
                "scale": self.scale, "min_scale": .5, "max_scale": 6}

    def avatar(self, identity: object) -> Path:
        choices = {str(path.parent): path.parent for path in self.catalog.vrm_manifests()}
        # Explicit --pet must pass the same resource validation before use.
        choices[str(self.directory)] = self.directory
        directory = choices.get(str(identity))
        if directory is None:
            raise ValueError("请选择目录中的有效 3D 形象")
        read_manifest(directory)
        return directory

    def preview(self, directory: Path) -> str:
        identity = str(directory)
        if identity not in self.servers:
            self.servers[identity] = AssetServer(directory, read_manifest(directory))
        return self.servers[identity].url

    def dispatch(self, method: str, params: dict[str, object]) -> dict[str, object]:
        with self.lock:
            if self.closed:
                raise RuntimeError("桌面服务已关闭")
            if method == "desktop_snapshot":
                return self.snapshot()
            if method == "desktop_avatars":
                paths = list(self.catalog.vrm_manifests())
                if self.directory / "pet.json" not in paths:
                    paths.append(self.directory / "pet.json")
                return {**self.snapshot(), "items": [
                    {"id": str(path.parent), "name": self.catalog.read_json(path).get("displayName", path.parent.name),
                     "description": self.catalog.read_json(path).get("description", "")}
                    for path in paths]}
            if method == "desktop_preview_avatar":
                directory = self.avatar(params.get("id"))
                self.preview_directory = directory
                url = self.preview(directory)
                self.release_unused_servers()
                return {"url": url}
            if method == "desktop_prepare_avatar":
                directory = self.avatar(params.get("id"))
                token = uuid.uuid4().hex
                self.prepared[token] = directory
                return {"id": str(directory), "token": token, "url": self.preview(directory)}
            if method == "desktop_commit_avatar":
                token = str(params.get("token", ""))
                directory = self.prepared.get(token)
                if directory is None:
                    raise ValueError("形象加载凭证已失效")
                if params.get("persist", True):
                    self.catalog.commit(directory)
                self.directory = directory
                self.prepared.pop(token)
                self.release_unused_servers()
                return self.snapshot()
            if method == "desktop_cancel_avatar":
                self.prepared.pop(str(params.get("token", "")), None)
                self.release_unused_servers()
                return {"status": "cancelled"}
            if method == "desktop_set_pet_scale":
                scale = params.get("scale")
                if not valid_pet_scale(scale):
                    raise ValueError("桌宠大小必须在 50%～600% 之间")
                assert isinstance(scale, (int, float))
                save_pet_scale(self.data_dir, scale)
                self.scale = float(scale)
                return self.snapshot()
            if method == "desktop_persona":
                source = self.editable_soul if self.editable_soul.is_file() else self.soul_file
                content = source.read_text(encoding="utf-8")
                return {"name": persona_name(content), "content": content, "path": str(source)}
            if method == "desktop_save_persona":
                content = update_persona_name(str(params.get("content", "")), str(params.get("name", "")))
                self.editable_soul.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.editable_soul.with_suffix(".desktop.tmp")
                temporary.write_text(content, encoding="utf-8")
                temporary.replace(self.editable_soul)
                self.soul_file = self.editable_soul
                return self.dispatch("desktop_persona", {})
            if method == "desktop_runs":
                records = SessionRunRecords(self.data_dir / "runs")
                return {"items": [{"id": str(directory),
                                   "manifest": json.loads((directory / "manifest.json").read_text()),
                                   "files": [p.name for p in directory.iterdir() if p.is_file()]}
                                  for directory in reversed(records.directories(str(params.get("session_id", ""))))]}
            if method == "desktop_run_directory":
                records = SessionRunRecords(self.data_dir / "runs")
                session_id = str(params.get("session_id", ""))
                identity = params.get("id")
                if identity:
                    directory = next((p for p in records.directories(session_id) if str(p) == identity), None)
                    if directory is None:
                        raise ValueError("运行记录不属于此会话")
                else:
                    directory = records.latest(session_id) if params.get("latest") else None
                    directory = directory or records.materialize_view(session_id)
                return {"path": str(directory)}
            if method == "desktop_log_directory":
                self.log_dir.mkdir(parents=True, exist_ok=True)
                return {"path": str(self.log_dir)}
            raise ValueError(f"未知桌面服务方法：{method}")

    def release_unused_servers(self) -> None:
        keep = {str(path) for path in (self.directory, self.preview_directory, *self.prepared.values()) if path is not None}
        for identity in list(self.servers):
            if identity not in keep:
                self.servers.pop(identity).close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.backend.close()
        finally:
            for server in self.servers.values():
                server.close()
            self.servers.clear()
            self.prepared.clear()
