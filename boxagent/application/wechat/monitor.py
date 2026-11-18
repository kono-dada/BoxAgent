"""Source lifecycle and durable handoff; never invokes an Agent."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from boxagent.domain.events.contracts import EventSink
from boxagent.domain.wechat.contracts import MessageSource

logger = logging.getLogger(__name__)


class SourceFactory(Protocol):
    def __call__(self) -> MessageSource: ...


class WeChatMonitor:
    def __init__(self, factory: SourceFactory, sink: EventSink | None, *, interval: float = 0.5) -> None:
        if not 0.1 <= interval <= 60:
            raise ValueError("Invalid monitoring interval")
        self.factory = factory
        self.sink = sink
        self.interval = interval
        self._source: MessageSource | None = None
        self._job: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.last_error = ""

    @property
    def running(self) -> bool:
        return self._job is not None and not self._job.done()

    async def poll_once(self) -> int:
        self.last_error = ""
        if self.sink is None:
            raise ValueError("Configure an EventSink before starting monitoring")
        if self._source is None:
            self._source = await asyncio.to_thread(self.factory)
        count = await asyncio.to_thread(self._source.poll)
        pending = await asyncio.to_thread(self._source.pending)
        for item in pending:
            accepted = False
            try:
                receipt = await self.sink.accept(item.event)
                accepted = receipt.accepted and receipt.event_id == item.event.event_id
                if not accepted:
                    self.last_error = "event_sink_not_accepted"
                    logger.warning("EventSink did not durably accept the exact WeChat event")
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("WeChat event sink failed; retaining source outbox record")
            await asyncio.to_thread(self._source.acknowledge, item.event.event_id, accepted=accepted)
        return count

    async def start(self) -> None:
        if self.running:
            return
        await self.poll_once()  # ready only after validated baseline and pending recovery
        self._stop.clear()
        self._job = asyncio.create_task(self._run(), name="wechat-monitor")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("WeChat monitoring failed; source cursors remain durable")
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval)
            except TimeoutError:
                continue

    async def close(self) -> None:
        self._stop.set()
        if self._job is not None:
            await self._job  # let any to_thread poll finish before closing its SQLite state
            self._job = None
        if self._source is not None:
            await asyncio.to_thread(self._source.close)
            self._source = None
