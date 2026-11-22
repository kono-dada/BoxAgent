"""Wake short-lived Agent runs from durable events; no connector assumptions."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

from boxagent.agent.runtime.contracts import EventAgent
from boxagent.agent.runtime.models import RuntimeRequest
from boxagent.domain.events.contracts import EventInbox
from boxagent.domain.events.models import EventBatch, EventDecision, EventEnvelope, EventReceipt

logger = logging.getLogger(__name__)


class EventContext(Protocol):
    async def prepare(self, batch: EventBatch) -> tuple[str, RuntimeRequest]: ...


class EventNotification(Protocol):
    async def __call__(self, batch: EventBatch, decision: EventDecision) -> None: ...


class DirectEventNotice(Protocol):
    async def __call__(self, batch: EventBatch) -> tuple[str, EventDecision]: ...


class EventSuppression(Protocol):
    async def __call__(self, batch: EventBatch) -> str | None: ...


class EventSource(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def status(self) -> dict[str, object]: ...


class ExternalEventIntegration:
    def __init__(self, runs: EventRuns, sources: Mapping[str, EventSource]) -> None:
        self.runs = runs
        self.sources = sources
        self.source_errors: dict[str, str] = {}
        self._recovery_jobs: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        await self.runs.start()
        for name, source in self.sources.items():
            try:
                await source.start()
            except Exception as error:
                self.source_errors[name] = type(error).__name__
                logger.exception("Event source %s failed to start", name)
                self._recovery_jobs.append(asyncio.create_task(self._recover_source(name, source)))

    async def _recover_source(self, name: str, source: EventSource) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await source.start()
                self.source_errors.pop(name, None)
                return
            except Exception as error:
                self.source_errors[name] = type(error).__name__

    async def close(self) -> None:
        for job in self._recovery_jobs:
            job.cancel()
        await asyncio.gather(*self._recovery_jobs, return_exceptions=True)
        self._recovery_jobs.clear()
        for source in reversed(list(self.sources.values())):
            await source.close()
        await self.runs.close()

    async def status(self) -> dict[str, object]:
        return {"enabled": True, "inbox": await asyncio.to_thread(self.runs.inbox.status),
                "worker_error": self.runs.last_error, "completed_runs": self.runs.completed_runs,
                "source_errors": dict(self.source_errors),
                "sources": {name: source.status() for name, source in self.sources.items()}}


class EventRuns:
    def __init__(self, inbox: EventInbox, context: EventContext, agent: EventAgent,
                 notify: EventNotification, *, batch_wait: float = 0.75,
                 direct_notices: Mapping[str, DirectEventNotice] | None = None,
                 suppressions: Mapping[str, EventSuppression] | None = None) -> None:
        self.inbox = inbox
        self.context = context
        self.agent = agent
        self.notify = notify
        self.batch_wait = batch_wait
        self.direct_notices = direct_notices if direct_notices is not None else {}
        self.suppressions = suppressions if suppressions is not None else {}
        self.direct_deliveries: dict[str, EventNotification] = {}
        self._wake = asyncio.Event()
        self._job: asyncio.Task[None] | None = None
        self._closing = False
        self.last_error = ""
        self.completed_runs = 0

    async def accept(self, event: EventEnvelope) -> EventReceipt:
        receipt = await asyncio.to_thread(self.inbox.put, event)
        self._wake.set()
        return receipt

    async def start(self) -> None:
        if self._job is not None and not self._job.done():
            return
        self._closing = False
        self._job = asyncio.create_task(self._run(), name="external-event-runs")
        self._wake.set()

    async def process_once(self) -> bool:
        batch = await asyncio.to_thread(self.inbox.claim)
        if batch is None:
            return False
        heartbeat = asyncio.create_task(self._renew(batch))
        try:
            source = batch.events[0].source
            suppress = self.suppressions.get(source)
            reason = await suppress(batch) if suppress is not None else None
            decision = EventDecision("silent", "", (), reason) if reason else batch.decision
            if decision is None:
                direct = self.direct_notices.get(source)
                if direct is not None:
                    session_id, decision = await direct(batch)
                    await asyncio.to_thread(self.inbox.bind_session, batch, session_id)
                    batch = replace(batch, session_id=session_id)
                else:
                    session_id, request = await self.context.prepare(batch)
                    await asyncio.to_thread(self.inbox.bind_session, batch, session_id)
                    batch = replace(batch, session_id=session_id)
                    decision = await self.agent.evaluate(request)
            validate_decision(decision, batch.events)
            if decision != batch.decision:
                await asyncio.to_thread(self.inbox.save_decision, batch, decision)
            await asyncio.to_thread(self.inbox.renew, batch)
            if decision.action == "notify":
                deliver = self.direct_deliveries.get(source, self.notify)
                await deliver(batch, decision)
            await asyncio.to_thread(self.inbox.complete, batch)
            self.completed_runs += 1
            self.last_error = ""
            return True
        except asyncio.CancelledError:
            try:
                await asyncio.to_thread(self.inbox.retry, batch, "cancelled")
            except ValueError:
                logger.warning("Cancelled event run no longer owns its lease")
            raise
        except Exception as error:
            self.last_error = type(error).__name__
            logger.exception("External event run failed; durable batch retained")
            await asyncio.to_thread(self.inbox.retry, batch, self.last_error)
            return False
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _renew(self, batch: EventBatch) -> None:
        while True:
            await asyncio.sleep(15)
            await asyncio.to_thread(self.inbox.renew, batch)

    async def _run(self) -> None:
        while not self._closing:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 2)
            self._wake.clear()
            if self._closing:
                break
            await asyncio.sleep(self.batch_wait)
            try:
                if await self.process_once():
                    self._wake.set()
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("Event inbox worker failed")

    async def close(self) -> None:
        self._closing = True
        self._wake.set()
        if self._job is not None:
            self._job.cancel()
            await asyncio.gather(self._job, return_exceptions=True)
            self._job = None


def validate_decision(decision: EventDecision, events: tuple[EventEnvelope, ...]) -> None:
    identifiers = {event.event_id for event in events}
    if decision.action not in {"notify", "silent"}:
        raise ValueError("Unknown external event decision")
    if not set(decision.source_event_ids) <= identifiers:
        raise ValueError("Agent cited an event outside the current batch")
    if decision.action == "notify" and (
        not decision.summary.strip() or not decision.source_event_ids
    ):
        raise ValueError("Notification lacks content or source evidence")
    if len(decision.summary) > 1000 or (decision.action == "silent" and decision.summary):
        raise ValueError("Invalid notification content budget")
