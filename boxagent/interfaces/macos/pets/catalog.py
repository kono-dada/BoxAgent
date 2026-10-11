"""本地 VRM 目录；渲染成功后保存选择，不访问远端形象服务。"""

import json
import hashlib
import os
from pathlib import Path
import re
import uuid

from .vrm import read_manifest, resource_files

ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def valid_id(value):
    if not isinstance(value, str) or len(value) > 160 or not ID_PATTERN.fullmatch(value):
        raise ValueError("形象 ID 无效")
    return value


class PetCatalog:
    def __init__(self, cache_dir=None, *, default_pet=None, bundled_root=None):
        project_root = Path(__file__).resolve().parents[4]
        self.default_pet = Path(default_pet or project_root / "assets/vrm/models/zome").resolve()
        self.bundled_root = Path(bundled_root or self.default_pet.parent).resolve()
        self.root = Path(cache_dir or project_root / ".runtime/pets").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._digests = {}

    def read_json(self, path):
        path = Path(path)
        if path.resolve() != path or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("形象元数据无效")
        return json.loads(path.read_text())

    def write_json(self, path, value):
        path = Path(path)
        if path.resolve() != path:
            raise ValueError("选择路径不能包含符号链接")
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def current_directory(self):
        try:
            saved = self.read_json(self.root / "selected.json")
            if saved.get("type") == "vrm":
                root = self.bundled_root if saved.get("bundled") else self.root
                directory = root / valid_id(saved["id"])
                if directory.resolve() != directory:
                    raise ValueError("形象路径不能包含符号链接")
                # 丢失的 VRM 由启动层报错；旧图集选择则迁移到默认 VRM。
                if not (directory / "pet.json").exists():
                    return directory
                if self.read_json(directory / "pet.json").get("type") == "vrm":
                    return self.canonical_directory(directory)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return self.default_pet

    def fingerprint(self, directory):
        """比较实际素材与渲染配置，不因同名误合并不同角色或动作变体。"""
        manifest = read_manifest(directory)
        files = resource_files(directory, manifest)

        def digest(path):
            stat = path.stat()
            key = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            cached = self._digests.get(path)
            if cached is None or cached[0] != key:
                with path.open("rb") as stream:
                    cached = (key, hashlib.file_digest(stream, "sha256").hexdigest())
                self._digests[path] = cached
            return cached[1]

        settings = {key: value for key, value in manifest.items()
                    if key not in {"displayName", "description", "model", "actions",
                                   "motionPack", "motionProfile"}}
        identity = {
            "model": digest(files[manifest["model"]]),
            "actions": {key: digest(files[value]) for key, value in manifest.get("actions", {}).items()},
            "settings": settings,
        }
        return json.dumps(identity, sort_keys=True, ensure_ascii=False)

    def vrm_manifests(self):
        """仓库版本优先；相同素材和配置只展示一次，不加载损坏的形象。"""
        result = []
        seen = set()
        for root in dict.fromkeys((self.bundled_root, self.root)):
            for path in sorted(root.glob("*/pet.json")):
                try:
                    valid_id(path.parent.name)
                    if self.read_json(path).get("type") != "vrm":
                        continue
                    identity = self.fingerprint(path.parent)
                    if identity not in seen:
                        seen.add(identity)
                        result.append(path)
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
        return result

    def canonical_directory(self, directory):
        try:
            identity = self.fingerprint(directory)
            for path in self.vrm_manifests():
                if self.fingerprint(path.parent) == identity:
                    return path.parent
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        # 保留缺失资源的明确选择，由加载层给出可操作的错误。
        return directory

    def commit(self, directory):
        directory = Path(directory)
        if directory.resolve() != directory or directory.parent not in (self.bundled_root, self.root):
            raise ValueError("形象不在允许的 VRM 目录内，保留原选择")
        identity = valid_id(directory.name)
        read_manifest(directory)
        directory = self.canonical_directory(directory)
        identity = directory.name
        saved = {"type": "vrm", "id": identity}
        if directory.parent == self.bundled_root:
            saved["bundled"] = True
        self.write_json(self.root / "selected.json", saved)
