"""Durable asynchronous Jev-Mem admission orchestration."""

import asyncio
import time
from dataclasses import replace

from boxagent.domain.memory.policy import redact_memory_source


class MemoryIngestionCoordinator:
    version = "jev-observation-v1"

    def __init__(self, conversation_store, job_store, *, service,
                 max_attempts=3, retry_base_seconds=1.0, log=None,
                 ):
        self.conversation_store = conversation_store
        self.job_store = job_store
        self._service = service
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.log = log or (lambda *_args, **_kwargs: None)
        self.tasks = set()
        self.active_job_ids = set()
        self.session_locks = {}
        self.closed = False

    async def start(self):
        self.closed = False
        await self.job_store.start()
        recoverable = await self.job_store.jobs(
            statuses={"pending", "running", "retry_wait", "failed"})
        for job in recoverable:
            if job.attempts < self.max_attempts:
                self._schedule(job)

    async def user_message_committed(self, *, session_id, interaction_id,
                                     event, source_hash):
        if self.closed:
            return
        job = await self.job_store.enqueue_job(
            session_id=session_id, interaction_id=interaction_id,
            source_hash=source_hash, admission_version=self.version)
        if job.status in {"pending", "running", "retry_wait", "failed"} \
                and job.attempts < self.max_attempts:
            self._schedule(job)

    async def interaction_finalized(self, **_context):
        """Narrative work is wired separately; admission starts at commit."""
        return None

    def _schedule(self, job):
        if job.job_id in self.active_job_ids:
            return
        self.active_job_ids.add(job.job_id)
        task = asyncio.create_task(self._run(job))
        self.tasks.add(task)

        def completed(done):
            self.tasks.discard(done)
            self.active_job_ids.discard(job.job_id)
        task.add_done_callback(completed)

    async def _run(self, initial):
        lock = self.session_locks.setdefault(initial.session_id, asyncio.Lock())
        async with lock:
            current = initial
            while not self.closed and current.attempts < self.max_attempts:
                delay = max(0.0, float(current.next_retry_at or 0) - time.time())
                if delay:
                    await asyncio.sleep(delay)
                running = replace(
                    current, status="running", attempts=current.attempts + 1,
                    updated_at=time.time(), error="", next_retry_at=None)
                await self.job_store.update_job(running)
                try:
                    result = await self._ingest(running)
                except asyncio.CancelledError:
                    await self.job_store.update_job(replace(
                        running, status="pending", updated_at=time.time(),
                        error="worker_cancelled", next_retry_at=None))
                    raise
                except Exception as exc:  # noqa: BLE001 - persist arbitrary provider failures
                    error = redact_memory_source(
                        f"{type(exc).__name__}: {exc}")[:500]
                    if running.attempts >= self.max_attempts:
                        current = replace(
                            running, status="failed", updated_at=time.time(),
                            error=error, next_retry_at=None)
                    else:
                        retry_at = time.time() + self.retry_base_seconds * (
                            2 ** (running.attempts - 1))
                        current = replace(
                            running, status="retry_wait", updated_at=time.time(),
                            error=error, next_retry_at=retry_at)
                    await self.job_store.update_job(current)
                    self.log(
                        "memory_ingestion_retry"
                        if current.status == "retry_wait"
                        else "memory_ingestion_failed",
                        job_id=current.job_id,
                        session_id=current.session_id,
                        interaction_id=current.interaction_id,
                        attempts=current.attempts,
                        next_retry_at=current.next_retry_at,
                        error=type(exc).__name__,
                    )
                    continue
                terminal = replace(
                    running,
                    status="skipped" if result["observation_count"] == 0 else "completed",
                    updated_at=time.time(), result=result, next_retry_at=None)
                await self.job_store.update_job(terminal)
                self.log("memory_ingestion_completed", job_id=terminal.job_id,
                         session_id=terminal.session_id,
                         interaction_id=terminal.interaction_id, **result)
                return

    async def _ingest(self, job):
        events = await self.conversation_store.read_events(job.session_id)
        target = next((item for item in events
                       if item.interaction_id == job.interaction_id
                       and item.type == "message.final"
                       and item.role == "user" and item.content), None)
        if target is None:
            return self._empty_counts()
        result = await self._service.observe_user_message(
            redact_memory_source(target.content),
            session_id=job.session_id,
            interaction_id=job.interaction_id,
            source_event_id=target.event_id,
            occurred_at=target.occurred_at,
            channel=target.source,
        )
        counts = self._empty_counts()
        counts["observation_count"] = 1
        counts["admitted"] = int(result.get("admitted") or 0)
        counts["rejected"] = int(result.get("rejected") or 0)
        return counts

    @staticmethod
    def _empty_counts():
        return {"observation_count": 0, "admitted": 0, "rejected": 0}

    async def drain(self):
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def health(self):
        jobs = await self.job_store.jobs()
        statuses = {}
        for job in jobs:
            statuses[job.status] = statuses.get(job.status, 0) + 1
        return {
            "closed": self.closed,
            "active_jobs": len(self.tasks),
            "job_statuses": statuses,
        }

    async def close(self):
        self.closed = True
        for task in tuple(self.tasks):
            task.cancel()
        await self.drain()
