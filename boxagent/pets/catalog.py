"""可独立测试的商店目录；下载与当前选择分离，窗口成功装配后才提交选择。"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from http.client import HTTPException
import io
import json
import logging
import os
from pathlib import Path
import re
import threading
import urllib.parse
import uuid

from PIL import Image

from ..config import DEFAULT_PET, ROOT
from .network import PetTransport, SOURCE

MAX_IMAGE = 12 * 1024 * 1024
ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def valid_id(value):
    if not isinstance(value, str) or len(value) > 160 or not ID_PATTERN.fullmatch(value):
        raise ValueError("形象 ID 无效")
    return value


def asset_url(value, identity, filename):
    url = urllib.parse.urljoin(SOURCE, value or f"/assets/pets/{identity}/{filename}")
    target = urllib.parse.urlsplit(url)
    pattern = rf"/assets/pets/(?:v/[0-9]+/)?{re.escape(identity)}/{re.escape(filename)}"
    if (target.scheme != "https" or target.netloc != "codex-pets.net"
            or target.query or target.fragment or not re.fullmatch(pattern, target.path)):
        raise ValueError("商店资源地址不在允许范围")
    return url


def normalize(raw):
    identity = valid_id(raw["id"])
    report = raw.get("validationReport") or {}
    version = raw.get("spriteVersionNumber", report.get("spriteVersionNumber",
                       2 if report.get("atlasSize") == "1536x2288" else 1))
    if type(version) is not int or version not in (1, 2):
        raise ValueError("暂不支持此图集版本")
    def text(key, fallback="", maximum=2000):
        value = raw.get(key)
        return value[:maximum] if isinstance(value, str) else fallback
    handle = text("ownerHandle", maximum=120)
    return dict(id=identity, displayName=text("displayName", identity, 160),
                description=text("description"), spriteVersionNumber=version,
                ownerName=text("ownerName", handle or "未注明", 160), ownerHandle=handle,
                sourceUrl=f"{SOURCE}/#/pets/{identity}", license=text("license") or None,
                spritesheetUrl=asset_url(raw.get("spritesheetUrl"), identity, "spritesheet.webp"),
                posterUrl=asset_url(raw["posterUrl"], identity, "poster.webp") if raw.get("posterUrl") else None)


def decode_image(data, version=None):
    if len(data) > MAX_IMAGE:
        raise ValueError("图集过大")
    with Image.open(io.BytesIO(data)) as image:
        if image.format != "WEBP" or getattr(image, "n_frames", 1) != 1:
            raise ValueError("图集必须是静态 WebP")
        if version is not None:
            expected = (1536, 2288 if version == 2 else 1872)
            if image.size != expected:
                raise ValueError("图集尺寸与版本不匹配")
        elif image.width > 768 or image.height > 832:
            raise ValueError("封面尺寸过大")
        image.load()
        return image.convert("RGBA")


class PetCatalog:
    def __init__(self, cache_dir=None, request=None):
        self.root = Path(cache_dir or ROOT / ".runtime/pets").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.request = request or PetTransport()
        self.lock = threading.RLock()

    def path(self, identity, filename):
        valid_id(identity)
        target = self.root / identity / filename
        if target.resolve() != target or (self.root / identity).is_symlink():
            raise ValueError("缓存路径不能包含符号链接")
        return target

    def atomic(self, target, data):
        target = Path(target)
        if target.resolve() != target:
            raise ValueError("缓存路径不能包含符号链接")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def write_json(self, path, value):
        self.atomic(path, json.dumps(value, ensure_ascii=False, indent=2).encode())

    def read_json(self, path):
        if path.resolve() != path or path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError("缓存元数据无效")
        return json.loads(path.read_text())

    def read_image(self, path):
        if path.stat().st_size > MAX_IMAGE:
            raise ValueError("缓存图片过大")
        return path.read_bytes()

    def cached(self, identity):
        try:
            entry = self.read_json(self.path(identity, "entry.json"))
            pet = normalize(entry)
            if pet["id"] != identity:
                return None
            data = self.read_image(self.path(identity, "spritesheet.webp"))
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                return None
            decode_image(data, pet["spriteVersionNumber"])
            manifest = self.read_json(self.path(identity, "pet.json"))
            if (manifest.get("spritesheetPath") != "spritesheet.webp"
                    or manifest.get("spriteVersionNumber") != pet["spriteVersionNumber"]):
                return None
            return dict(pet, directory=self.root / identity, cached=True)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def installed(self, search=""):
        result = []
        for path in sorted(self.root.iterdir()):
            if path.is_dir() and not path.is_symlink() and ID_PATTERN.fullmatch(path.name):
                pet = self.cached(path.name)
                if pet and search.casefold() in " ".join(str(pet[key]) for key in
                                                       ("id", "displayName", "description", "ownerName")).casefold():
                    result.append(pet)
        return result

    def current_directory(self):
        try:
            saved = self.read_json(self.root / "selected.json")
            if saved.get("builtin"):
                return DEFAULT_PET
            pet = self.cached(saved["id"])
            if pet:
                return pet["directory"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        return DEFAULT_PET

    def commit(self, directory):
        directory = Path(directory).resolve()
        if directory == DEFAULT_PET.resolve():
            self.write_json(self.root / "selected.json", {"builtin": True})
        else:
            if directory.parent != self.root or not self.cached(directory.name):
                raise ValueError("形象缓存无效，保留原选择")
            self.write_json(self.root / "selected.json", {"id": directory.name})

    def prepare(self, identity):
        valid_id(identity)
        with self.lock:
            if pet := self.cached(identity):
                return pet["directory"]
            raw = json.loads(self.request(f"{SOURCE}/api/pets/{identity}/share-data", 2 * 1024 * 1024))
            pet = normalize(raw["pet"])
            if pet["id"] != identity:
                raise ValueError("商店返回了另一个形象")
            data = self.request(pet["spritesheetUrl"], MAX_IMAGE)
            decode_image(data, pet["spriteVersionNumber"])
            pet["sha256"] = hashlib.sha256(data).hexdigest()
            self.atomic(self.path(identity, "spritesheet.webp"), data)
            self.write_json(self.path(identity, "pet.json"), dict(
                id=identity, displayName=pet["displayName"], description=pet["description"],
                spriteVersionNumber=pet["spriteVersionNumber"], spritesheetPath="spritesheet.webp"))
            self.write_json(self.path(identity, "entry.json"), pet)
            return self.root / identity

    def poster(self, pet):
        identity = pet["id"]
        try:
            path = self.path(identity, "poster.webp")
            meta = self.path(identity, "poster.json")
            if path.exists() and meta.exists():
                data = self.read_image(path)
                saved = self.read_json(meta)
                if saved.get("url") == pet["posterUrl"] and saved.get("sha256") == hashlib.sha256(data).hexdigest():
                    return decode_image(data)
            if pet["posterUrl"]:
                data = self.request(pet["posterUrl"], 512 * 1024)
                picture = decode_image(data)
                self.atomic(path, data)
                self.write_json(meta, dict(url=pet["posterUrl"], sha256=hashlib.sha256(data).hexdigest()))
                return picture
        except (OSError, ValueError, KeyError, TypeError, HTTPException):
            pass
        if pet.get("cached"):
            try:
                return decode_image(self.read_image(self.path(identity, "spritesheet.webp")),
                                    pet["spriteVersionNumber"]).crop((0, 0, 192, 208))
            except (OSError, ValueError):
                pass
        return None

    def list(self, search="", page=1, installed=False):
        search, page = search.strip()[:120], max(1, int(page))
        error = None
        pets = []
        if not installed:
            try:
                query = urllib.parse.urlencode(dict(page=page, pageSize=12, q=search, sort="popular"))
                raw = json.loads(self.request(f"{SOURCE}/api/pets?{query}", 2 * 1024 * 1024))
                for item in raw["pets"][:12]:
                    try:
                        pet = normalize(item)
                        pet["cached"] = self.cached(pet["id"]) is not None
                        pets.append(pet)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
                total = max(len(pets), int(raw["total"]))
            except (OSError, ValueError, KeyError, TypeError, AttributeError, HTTPException):
                logging.getLogger(__name__).exception("商店目录请求失败，回退到已下载形象")
                error = "暂时连不上形象商店。你可以使用已下载的形象，或稍后重新搜索。"
        if installed or error:
            available = self.installed(search)
            total = len(available)
            page = min(page, max(1, (total + 11) // 12))
            pets = available[(page - 1) * 12:page * 12]
        # 已下载列表完全离线，不因缺封面访问网络。
        def preview(pet):
            if installed or error:
                try:
                    return decode_image(self.read_image(self.path(pet["id"], "spritesheet.webp")),
                                        pet["spriteVersionNumber"]).crop((0, 0, 192, 208))
                except (OSError, ValueError):
                    return None
            return self.poster(pet)
        def thumbnail(pet):
            picture = preview(pet)
            if picture is not None:
                picture.thumbnail((200, 156))
            return picture
        with ThreadPoolExecutor(max_workers=4) as pool:
            pictures = list(pool.map(thumbnail, pets))
        return dict(pets=[dict(pet, picture=picture) for pet, picture in zip(pets, pictures)],
                    total=total, page=page, error=error)
