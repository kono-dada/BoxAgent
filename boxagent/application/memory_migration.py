"""One-time import of legacy Canonical Ledger records into Jev-Mem."""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


class LegacyCanonicalMemoryImporter:
    """Import upgrade data without making the retired ledger a live store."""

    schema_version = 1

    def __init__(self, backend, snapshot_path, marker_path, *,
                 profile_projector=None, log=None):
        self.backend = backend
        self.snapshot_path = Path(snapshot_path)
        self.marker_path = Path(marker_path)
        self.profile_projector = profile_projector
        self.log = log or (lambda *_args, **_kwargs: None)
        self.task = None
        self.result = {"status": "not_started", "imported": 0,
                       "already_present": 0, "skipped": 0}

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(
                self._run_guarded(), name="boxagent-legacy-memory-import")

    async def drain(self):
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
        await self.drain()

    async def health(self):
        return dict(self.result)

    async def _run_guarded(self):
        try:
            self.result = await self._run()
        except asyncio.CancelledError:
            self.result = {**self.result, "status": "cancelled"}
            raise
        except Exception as exc:  # noqa: BLE001 - retry on the next boot
            self.result = {**self.result, "status": "failed",
                           "error": type(exc).__name__}
            self.log("legacy_memory_import_failed", error=type(exc).__name__)

    async def _run(self):
        if not self.snapshot_path.is_file():
            return {"status": "not_needed", "imported": 0,
                    "already_present": 0, "skipped": 0}
        payload, source_hash = await asyncio.to_thread(self._read_snapshot)
        marker = await asyncio.to_thread(self._read_marker)
        processed = dict(marker.get("processed") or {})
        counts = {"imported": 0, "already_present": 0, "skipped": 0}

        for memory_id, record in sorted(payload["records"].items()):
            revision = int(record.get("revision") or 0)
            key = f"{memory_id}:{revision}"
            if key in processed:
                continue
            status = str(record.get("status") or "")
            content = str(record.get("content") or "").strip()
            if status != "active" or not content:
                outcome = "skipped"
                counts[outcome] += 1
            else:
                existing = await self._existing_link(record)
                if existing is not None:
                    await self._project(existing)
                    outcome = "already_present"
                    counts[outcome] += 1
                else:
                    observation = self._observation(memory_id, record)
                    result = await self._remember_once(
                        memory_id, content, observation)
                    created = list(result.get("created") or [])
                    for node in created:
                        await self._project(node)
                    outcome = "imported" if created else "skipped"
                    counts[outcome] += 1
            processed[key] = {
                "outcome": outcome,
                "processed_at": time.time(),
            }
            await asyncio.to_thread(
                self._write_marker, source_hash, processed, False)

        await asyncio.to_thread(
            self._write_marker, source_hash, processed, True)
        result = {"status": "completed", **counts,
                  "processed": len(processed)}
        self.log("legacy_memory_import_completed", **result)
        return result

    async def _existing_link(self, record):
        for memory_id in record.get("backend_links") or []:
            graph = await self.backend.inspect(
                selected_id=str(memory_id), node_limit=2, edge_limit=0)
            node = next((item for item in graph.get("nodes", [])
                         if item.get("id") == memory_id), None)
            if node is not None:
                return node
        return None

    async def _project(self, node):
        if self.profile_projector is not None:
            await self.profile_projector.observe(node)

    async def _remember_once(self, memory_id, content, observation):
        try:
            return await self.backend.remember([observation])
        except asyncio.CancelledError:
            raise
        except Exception:
            # The worker may have persisted the node before its response timed
            # out. Query by exact content and migration metadata before retrying.
            graph = await self.backend.inspect(
                query=content[:500], node_limit=20, edge_limit=0)
            existing = next((item for item in graph.get("nodes", [])
                             if item.get("metadata", {}).get(
                                 "legacy_canonical_id") == memory_id), None)
            if existing is not None:
                return {"admitted": 1, "rejected": 0,
                        "created": [existing]}
            return await self.backend.remember([observation])

    def _read_snapshot(self):
        raw = self.snapshot_path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(
                payload.get("records"), dict):
            raise ValueError("Legacy Canonical Memory Snapshot 格式无效")
        return payload, hashlib.sha256(raw).hexdigest()

    def _read_marker(self):
        if not self.marker_path.is_file():
            return {"processed": {}}
        payload = json.loads(self.marker_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != self.schema_version \
                or not isinstance(payload.get("processed"), dict):
            raise ValueError("Legacy Memory Migration marker 格式无效")
        return payload

    def _write_marker(self, source_hash, processed, completed):
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.marker_path.with_name(self.marker_path.name + ".tmp")
        payload = {
            "schema_version": self.schema_version,
            "migration": "canonical-ledger-to-jev-v1",
            "source_snapshot": str(self.snapshot_path),
            "source_sha256": source_hash,
            "completed": completed,
            "updated_at": time.time(),
            "processed": processed,
        }
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False,
                      separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.marker_path)

    @staticmethod
    def _observation(memory_id, record):
        timestamp = record.get("updated_at") or record.get("created_at")
        occurred_at = None
        if timestamp:
            occurred_at = datetime.fromtimestamp(
                float(timestamp), timezone.utc).isoformat()
        metadata = {
            "source": "legacy_canonical_migration",
            "role": "user",
            "explicit": True,
            "legacy_canonical_id": memory_id,
            "legacy_revision": int(record.get("revision") or 0),
            "legacy_kind": record.get("kind"),
            "legacy_source_mode": record.get("source_mode"),
            "session_id": record.get("source_session_id") or "",
            "interaction_id": record.get("source_interaction_id") or "",
            "source_event_ids": list(record.get("source_event_ids") or []),
        }
        observation = {"content": str(record["content"]),
                       "metadata": metadata}
        if occurred_at:
            observation["timestamp"] = occurred_at
        return observation
