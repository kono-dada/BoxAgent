"""User Profile projection built from admitted Jev-Mem observations."""

import asyncio
import json
import logging
import os
import re
import time
import urllib.request
from collections.abc import Awaitable, Callable
from pathlib import Path

from boxagent.domain.memory.contracts import MemoryUnavailable, ProfileConstructor

logger = logging.getLogger(__name__)


ALLOWED_PREFIXES = (
    "identity.", "preferences.", "constraints.", "goals.",
    "relationships.", "shared_commitments.",
)


class JsonProfileStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def load(self):
        async with self.lock:
            return await asyncio.to_thread(self._read)

    async def apply(self, patches, *, memory_id):
        async with self.lock:
            profile = await asyncio.to_thread(self._read)
            fields = dict(profile.get("fields") or {})
            changed, removed = [], []
            for patch in patches:
                operation = patch.get("op")
                path = str(patch.get("path") or "").strip().strip("/").replace("/", ".")
                if not path.startswith(ALLOWED_PREFIXES):
                    continue
                if operation == "remove":
                    if fields.pop(path, None) is not None:
                        removed.append(path)
                    continue
                if operation not in {"add", "replace"} or "value" not in patch:
                    continue
                value = patch["value"]
                if not isinstance(value, (str, int, float, bool, list)):
                    continue
                current = dict(fields.get(path) or {})
                sources = list(dict.fromkeys([
                    *current.get("source_memory_ids", []), memory_id]))
                fields[path] = {
                    "value": value,
                    "confidence": min(1.0, max(0.0, float(
                        patch.get("confidence", 0.8)))),
                    "source_memory_ids": sources,
                    "updated_at": time.time(),
                }
                changed.append({"path": path, "value": value})
            if not changed and not removed:
                return {"version": int(profile.get("version") or 0),
                        "changed": [], "removed": []}
            profile = {"schema_version": 1,
                       "version": int(profile.get("version") or 0) + 1,
                       "fields": fields}
            await asyncio.to_thread(self._write, profile)
            return {"version": profile["version"], "changed": changed,
                    "removed": removed}

    async def forget(self, memory_id):
        async with self.lock:
            profile = await asyncio.to_thread(self._read)
            fields, removed = dict(profile.get("fields") or {}), []
            for path, record in tuple(fields.items()):
                sources = [item for item in record.get("source_memory_ids", [])
                           if item != memory_id]
                if not sources:
                    fields.pop(path, None)
                    removed.append(path)
                elif len(sources) != len(record.get("source_memory_ids", [])):
                    fields[path] = {**record, "source_memory_ids": sources}
            if removed:
                profile = {"schema_version": 1,
                           "version": int(profile.get("version") or 0) + 1,
                           "fields": fields}
                await asyncio.to_thread(self._write, profile)
            return removed

    def _read(self):
        if not self.path.is_file():
            return {"schema_version": 1, "version": 0, "fields": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "version": 0, "fields": {}}
        return value if isinstance(value, dict) else {
            "schema_version": 1, "version": 0, "fields": {}}

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)


class DeepSeekProfileConstructor:
    """Generate a bounded JSON Patch only from a successful model response."""

    def __init__(self, *, api_key: str = "", base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-flash", timeout: float = 12) -> None:
        self.api_key = str(api_key or "")
        self.base_url = str(base_url).rstrip("/")
        self.model = model
        self.timeout = timeout

    async def propose(self, observation: str, current_fields: dict) -> list[dict]:
        if not self.api_key:
            raise MemoryUnavailable("用户画像生成需要模型密钥，未更新画像")
        try:
            return await asyncio.to_thread(self._request, observation, current_fields)
        except Exception as exc:
            logger.warning("profile proposal failed; profile unchanged", exc_info=True)
            raise MemoryUnavailable("用户画像生成失败，未更新画像") from exc

    def _request(self, observation: str, current_fields: dict) -> list[dict]:
        prompt = {
            "observation": observation,
            "current_profile_fields": current_fields,
            "allowed_prefixes": list(ALLOWED_PREFIXES),
            "rules": [
                "Only record information explicitly stated by the user.",
                "Do not infer sensitive traits, diagnoses, politics, income or sexuality.",
                "Return JSON only: {patches:[{op,path,value,confidence}]}",
                "Use add, replace, or remove. Keep values compact.",
            ],
        }
        body = json.dumps({
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You construct a compact user profile from one admitted observation."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }).encode()
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=body,
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read())
        content = payload["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        result = json.loads(content)
        patches = result.get("patches") if isinstance(result, dict) else None
        if not isinstance(patches, list) or any(not isinstance(patch, dict) for patch in patches):
            raise ValueError("profile response must contain a patches list of objects")
        return patches


class ProfileProjector:
    def __init__(self, store: JsonProfileStore, constructor: ProfileConstructor, *,
                 on_delta: Callable[[dict], Awaitable[None] | None] | None = None) -> None:
        self.store = store
        self.constructor = constructor
        self.on_delta = on_delta

    async def observe(self, node):
        metadata = dict(node.get("metadata") or {})
        scores = dict(metadata.get("jev_mem", {}).get("memory_type") or {})
        if max((float(scores.get(name) or 0)
                for name in ("preference", "semantic", "procedural")),
               default=0) < 0.5:
            return None
        profile = await self.store.load()
        patches = await self.constructor.propose(
            str(node.get("content") or ""), profile.get("fields", {}))
        delta = await self.store.apply(patches, memory_id=str(node.get("id") or ""))
        if (delta.get("changed") or delta.get("removed")) and self.on_delta:
            result = self.on_delta(delta)
            if asyncio.iscoroutine(result):
                await result
        return delta

    async def forget(self, memory_id):
        return await self.store.forget(memory_id)

    async def snapshot(self):
        return await self.store.load()
