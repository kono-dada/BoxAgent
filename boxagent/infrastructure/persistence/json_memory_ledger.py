"""Append-only extraction jobs and canonical memory snapshot persistence."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from boxagent.core.ids import new_extraction_job_id
from boxagent.domain.memory.models import (
    MEMORY_SCHEMA_VERSION,
    CanonicalMemory,
    MemoryExtractionJob,
)


class JsonMemoryLedger:
    def __init__(self, root):
        self.root = Path(root)
        self.events_path = self.root / "ledger/events.jsonl"
        self.snapshot_path = self.root / "ledger/snapshot.json"
        self.jobs_path = self.root / "extraction/jobs.jsonl"
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
            self.events_path.touch(mode=0o600, exist_ok=True)
            self.jobs_path.touch(mode=0o600, exist_ok=True)
            records = self._read_memory_events(repair=True)
            self._write_json(self.snapshot_path, {
                "schema_version": MEMORY_SCHEMA_VERSION,
                "records": {item.memory_id: item.payload() for item in records},
            })
            self._read_jobs(repair=True)

    async def enqueue_job(self, *, session_id, interaction_id, source_hash,
                          extractor_version):
        key = hashlib.sha256(
            f"{extractor_version}:{session_id}:{interaction_id}:{source_hash}".encode()
        ).hexdigest()
        async with self.lock:
            jobs = self._latest_jobs(self._read_jobs(repair=True))
            duplicate = next((item for item in jobs.values()
                              if item.idempotency_key == key), None)
            if duplicate:
                return duplicate
            now = time.time()
            job = MemoryExtractionJob(
                job_id=new_extraction_job_id(), idempotency_key=key,
                session_id=session_id, interaction_id=interaction_id,
                source_hash=source_hash, extractor_version=extractor_version,
                status="pending", attempts=0, created_at=now, updated_at=now)
            self._append(self.jobs_path, job.payload())
            return job

    async def jobs(self, *, statuses=None):
        async with self.lock:
            jobs = list(self._latest_jobs(self._read_jobs(repair=True)).values())
            if statuses is not None:
                jobs = [item for item in jobs if item.status in statuses]
            return sorted(jobs, key=lambda item: item.created_at)

    async def update_job(self, job):
        async with self.lock:
            jobs = self._latest_jobs(self._read_jobs(repair=True))
            if job.job_id not in jobs:
                raise ValueError("Memory Extraction Job 不存在")
            if job.updated_at < jobs[job.job_id].updated_at:
                raise ValueError("不能写入过时的 Memory Extraction Job")
            self._append(self.jobs_path, job.payload())
            return job

    async def memories(self, *, statuses=None):
        async with self.lock:
            records = [CanonicalMemory.from_payload(item)
                       for item in self._read_snapshot()["records"].values()]
            if statuses is not None:
                records = [item for item in records if item.status in statuses]
            return sorted(records, key=lambda item: item.updated_at, reverse=True)

    async def memory(self, memory_id):
        async with self.lock:
            value = self._read_snapshot()["records"].get(memory_id)
            return CanonicalMemory.from_payload(value) if value else None

    async def save_memory(self, memory):
        async with self.lock:
            snapshot = self._read_snapshot()
            current_value = snapshot["records"].get(memory.memory_id)
            current = CanonicalMemory.from_payload(current_value) if current_value else None
            if current and memory.revision <= current.revision:
                if memory == current:
                    return current
                raise ValueError("Canonical Memory revision 必须递增")
            event = {
                "schema_version": MEMORY_SCHEMA_VERSION,
                "event": "memory.saved",
                "occurred_at": time.time(),
                "memory": memory.payload(),
            }
            self._append(self.events_path, event)
            snapshot["records"][memory.memory_id] = memory.payload()
            self._write_json(self.snapshot_path, snapshot)
            return memory

    def _read_snapshot(self):
        value = self._read_json(self.snapshot_path)
        if value.get("schema_version") != MEMORY_SCHEMA_VERSION \
                or not isinstance(value.get("records"), dict):
            raise ValueError("Canonical Memory Snapshot 格式无效")
        return value

    def _read_jobs(self, *, repair=False):
        return self._read_jsonl(
            self.jobs_path, MemoryExtractionJob.from_payload, repair=repair)

    def _read_memory_events(self, *, repair=False):
        def factory(value):
            if value.get("schema_version") != MEMORY_SCHEMA_VERSION \
                    or value.get("event") != "memory.saved" \
                    or not isinstance(value.get("memory"), dict):
                raise ValueError("Canonical Memory Event 格式无效")
            return CanonicalMemory.from_payload(value["memory"])

        events = self._read_jsonl(self.events_path, factory, repair=repair)
        latest = {}
        for item in events:
            current = latest.get(item.memory_id)
            if current is None or item.revision > current.revision:
                latest[item.memory_id] = item
        return list(latest.values())

    @staticmethod
    def _latest_jobs(jobs):
        return {item.job_id: item for item in jobs}

    @staticmethod
    def _read_json(path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} 必须是 JSON object")
        return value

    @classmethod
    def _read_jsonl(cls, path, factory, *, repair=False):
        if not path.exists():
            return []
        lines, values, valid = path.read_text(encoding="utf-8").splitlines(), [], []
        truncated = False
        for position, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                values.append(factory(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                if position == len(lines) - 1:
                    truncated = True
                    break
                raise ValueError(f"{path.name} 第 {position + 1} 行损坏")
            valid.append(line)
        if truncated and repair:
            cls._write_lines(path, valid)
        return values

    @staticmethod
    def _append(path, value):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @classmethod
    def _write_lines(cls, path, lines):
        temporary = path.with_name(path.name + ".repair")
        with temporary.open("w", encoding="utf-8") as stream:
            for line in lines:
                stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
