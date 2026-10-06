"""Durable asynchronous extraction and index retry orchestration."""

import asyncio
import time
from dataclasses import replace

from boxagent.domain.memory.policy import memory_admission, redact_memory_source
from boxagent.domain.memory.service import MemoryService


class MemoryExtractionCoordinator:
    def __init__(self, conversation_store, ledger, extractor, backend, *,
                 max_attempts=3, retry_base_seconds=1.0, log=None,
                 memory_service=None):
        self.conversation_store = conversation_store
        self.ledger = ledger
        self.extractor = extractor
        self.memory_service = memory_service or MemoryService(backend, ledger)
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.log = log or (lambda *_args, **_kwargs: None)
        self.tasks, self.index_tasks = set(), set()
        self.active_job_ids, self.active_memory_ids = set(), set()
        self.session_locks = {}
        self.closed = False

    async def start(self):
        self.closed = False
        await self.ledger.start()
        recoverable = await self.ledger.jobs(
            statuses={"pending", "running", "retry_wait", "failed"})
        for job in recoverable:
            if job.attempts < self.max_attempts:
                self._schedule(job)
        for memory in await self.ledger.memories(statuses={"active", "index_failed"}):
            if memory.metadata.get("index_state") in {"pending", "failed"} \
                    and int(memory.metadata.get("index_attempts") or 0) < self.max_attempts:
                self._schedule_index(memory.memory_id)

    async def interaction_completed(self, *, session_id, interaction_id,
                                    source_hash):
        if self.closed:
            return
        job = await self.ledger.enqueue_job(
            session_id=session_id, interaction_id=interaction_id,
            source_hash=source_hash, extractor_version=self.extractor.version)
        if job.status in {"pending", "running", "retry_wait", "failed"} \
                and job.attempts < self.max_attempts:
            self._schedule(job)

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

    def _schedule_index(self, memory_id):
        if memory_id in self.active_memory_ids:
            return
        self.active_memory_ids.add(memory_id)
        task = asyncio.create_task(self._run_index_retry(memory_id))
        self.index_tasks.add(task)

        def completed(done):
            self.index_tasks.discard(done)
            self.active_memory_ids.discard(memory_id)
        task.add_done_callback(completed)

    def schedule_index(self, memory_id):
        if not self.closed:
            self._schedule_index(memory_id)

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
                await self.ledger.update_job(running)
                try:
                    result = await self._extract(running)
                except asyncio.CancelledError:
                    await self.ledger.update_job(replace(
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
                    await self.ledger.update_job(current)
                    self.log("memory_extraction_retry" if current.status == "retry_wait"
                             else "memory_extraction_failed",
                             job_id=current.job_id, session_id=current.session_id,
                             interaction_id=current.interaction_id,
                             attempts=current.attempts, next_retry_at=current.next_retry_at,
                             error=type(exc).__name__)
                    continue
                terminal = replace(
                    running,
                    status="skipped" if result["candidate_count"] == 0 else "completed",
                    updated_at=time.time(), result=result, next_retry_at=None)
                await self.ledger.update_job(terminal)
                self.log("memory_extraction_completed", job_id=terminal.job_id,
                         session_id=terminal.session_id,
                         interaction_id=terminal.interaction_id, **result)
                return

    async def _run_index_retry(self, memory_id):
        while not self.closed:
            memory = await self.ledger.memory(memory_id)
            if memory is None or memory.status not in {"active", "index_failed"} \
                    or memory.metadata.get("index_state") not in {"pending", "failed"}:
                return
            attempts = int(memory.metadata.get("index_attempts") or 0)
            if attempts >= self.max_attempts:
                return
            retry_at = float(memory.metadata.get("index_next_retry_at") or 0)
            delay = max(0.0, retry_at - time.time())
            if delay:
                await asyncio.sleep(delay)
            result = await self.memory_service.retry_index(memory_id)
            memory = await self.ledger.memory(memory_id)
            self.log("memory_index_retry", memory_id=memory_id,
                     status=result["status"],
                     attempts=int(memory.metadata.get("index_attempts") or 0))
            if result["status"] == "succeeded":
                return

    async def _extract(self, job):
        events = await self.conversation_store.read_events(job.session_id)
        target = tuple(item for item in events
                       if item.interaction_id == job.interaction_id
                       and item.type == "message.final"
                       and item.role in {"user", "assistant"} and item.content)
        if not any(item.role == "user" for item in target):
            return self._empty_counts()
        target_ids = {item.event_id for item in target}
        prior = [item for item in events
                 if item.type == "message.final" and item.content
                 and item.event_id not in target_ids
                 and item.sequence < target[0].sequence][-6:]
        existing = tuple(await self.ledger.memories(
            statuses={"active", "index_failed"}))
        candidates = await self.extractor.extract(
            target_messages=target, context_messages=tuple(prior),
            existing_memories=existing)
        counts = self._empty_counts()
        counts["candidate_count"] = len(candidates)
        target_by_id = {item.event_id: redact_memory_source(item.content)
                        for item in target if item.role == "user"}
        for candidate in candidates:
            status = memory_admission(candidate)
            if not self._evidence_matches(candidate, target_by_id):
                status = "pending_review" if status != "rejected" else status
            if status == "rejected":
                counts["rejected"] += 1
                continue
            result = await self.memory_service.admit_candidate(
                candidate, status=status, source_mode="automatic",
                session_id=job.session_id, interaction_id=job.interaction_id,
                source_event_ids=tuple(dict.fromkeys(
                    item["event_id"] for item in candidate.evidence)))
            if result["outcome"] == "duplicate":
                counts["duplicates"] += 1
                continue
            final_status = result["memory"].status
            counts[final_status] += 1
            if final_status == "index_failed":
                self._schedule_index(result["memory"].memory_id)
        return counts

    @staticmethod
    def _empty_counts():
        return {"candidate_count": 0, "active": 0,
                "pending_review": 0, "rejected": 0,
                "duplicates": 0, "index_failed": 0}

    @staticmethod
    def _evidence_matches(candidate, target_by_id):
        return bool(candidate.evidence) and all(
            item.get("event_id") in target_by_id
            and str(item.get("quote") or "").strip()
            and str(item.get("quote") or "").strip() in target_by_id[item["event_id"]]
            for item in candidate.evidence)

    async def drain(self):
        while self.tasks or self.index_tasks:
            await asyncio.gather(
                *tuple(self.tasks), *tuple(self.index_tasks),
                return_exceptions=True)

    async def close(self):
        self.closed = True
        for task in (*tuple(self.tasks), *tuple(self.index_tasks)):
            task.cancel()
        await self.drain()
