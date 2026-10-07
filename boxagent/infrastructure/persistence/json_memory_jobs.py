"""Append-only durable delivery state for asynchronous memory admission."""

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from boxagent.core.ids import new_memory_job_id
from boxagent.domain.memory.models import MEMORY_SCHEMA_VERSION, MemoryIngestionJob


class JsonMemoryJobStore:
    def __init__(self, root):
        self.root = Path(root)
        self.jobs_path = self.root / "jobs/ingestion.jsonl"
        self.legacy_jobs_path = self.root / "extraction/jobs.jsonl"
        self.lock = asyncio.Lock()

    async def start(self):
        async with self.lock:
            self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.jobs_path.exists() and self.legacy_jobs_path.is_file():
                self.jobs_path.write_bytes(self.legacy_jobs_path.read_bytes())
            self.jobs_path.touch(mode=0o600, exist_ok=True)
            self._read_jobs(repair=True)

    async def enqueue_job(self, *, session_id, interaction_id, source_hash,
                          admission_version="jev-observation-v1"):
        version = admission_version
        key = hashlib.sha256(
            f"{version}:{session_id}:{interaction_id}:{source_hash}".encode()
        ).hexdigest()
        async with self.lock:
            jobs = self._latest_jobs(self._read_jobs(repair=True))
            duplicate = next((item for item in jobs.values()
                              if item.idempotency_key == key), None)
            if duplicate:
                return duplicate
            now = time.time()
            job = MemoryIngestionJob(
                job_id=new_memory_job_id(), idempotency_key=key,
                session_id=session_id, interaction_id=interaction_id,
                source_hash=source_hash, admission_version=version,
                status="pending", attempts=0, created_at=now, updated_at=now)
            self._append(job.payload())
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
                raise ValueError("Memory Ingestion Job 不存在")
            if job.updated_at < jobs[job.job_id].updated_at:
                raise ValueError("不能写入过时的 Memory Ingestion Job")
            self._append(job.payload())
            return job

    def _read_jobs(self, *, repair=False):
        if not self.jobs_path.exists():
            return []
        lines = self.jobs_path.read_text(encoding="utf-8").splitlines()
        values, valid = [], []
        for position, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                values.append(MemoryIngestionJob.from_payload(json.loads(line)))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                if position == len(lines) - 1 and repair:
                    self._write_lines(valid)
                    break
                raise ValueError(f"{self.jobs_path.name} 第 {position + 1} 行损坏")
            valid.append(line)
        return values

    @staticmethod
    def _latest_jobs(jobs):
        return {item.job_id: item for item in jobs}

    def _append(self, value):
        with self.jobs_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _write_lines(self, lines):
        temporary = self.jobs_path.with_name(self.jobs_path.name + ".repair")
        with temporary.open("w", encoding="utf-8") as stream:
            for line in lines:
                stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.jobs_path)
