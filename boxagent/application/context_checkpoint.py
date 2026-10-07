"""Asynchronous Product Session checkpoint orchestration."""

import asyncio
import hashlib
import json
import time

from boxagent.core.ids import new_checkpoint_id, new_event_id, new_segment_id
from boxagent.domain.conversation.models import (
    ContextCheckpoint,
    ProductEvent,
    RuntimeContextSegment,
)


class ContextCheckpointCoordinator:
    """Compact completed Product messages without delaying the foreground reply."""

    def __init__(self, store, generator, *, runtime="qwen_realtime",
                 trigger_characters=18000, source_character_limit=32000,
                 log=None, checkpoint_sinks=()):
        if trigger_characters <= 0 or source_character_limit <= 0:
            raise ValueError("Checkpoint 字符预算必须大于 0")
        if source_character_limit < trigger_characters:
            raise ValueError("Checkpoint 输入上限不能小于触发阈值")
        self.store = store
        self.generator = generator
        self.runtime = runtime
        self.trigger_characters = trigger_characters
        self.source_character_limit = source_character_limit
        self.log = log or (lambda *_args, **_kwargs: None)
        self.checkpoint_sinks = tuple(checkpoint_sinks)
        self.tasks = set()
        self.locks = {}
        self.closed = False

    async def start(self):
        self.closed = False

    async def interaction_finalized(self, *, session_id, interaction_id,
                                    source_hash):
        """Queue work and return immediately; generation never blocks a reply."""
        del source_hash
        if self.closed:
            return
        task = asyncio.create_task(self._run(session_id, interaction_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(self, session_id, interaction_id):
        lock = self.locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            try:
                await self._checkpoint_ready_prefixes(session_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.log("context_checkpoint_failed", session_id=session_id,
                         interaction_id=interaction_id, error=repr(exc))

    async def _checkpoint_ready_prefixes(self, session_id):
        while not self.closed:
            previous = await self.store.latest_checkpoint(session_id, self.runtime)
            cursor = previous.covered_through_sequence if previous else 0
            events = await self.store.read_events(session_id, after_sequence=cursor)
            messages = tuple(event for event in events
                             if event.type == "message.final"
                             and event.role in {"user", "assistant"}
                             and isinstance(event.content, str)
                             and event.content.strip())
            if sum(len(item.content) for item in messages) < self.trigger_characters:
                return
            batch = self._bounded_prefix(messages)
            content = await self.generator.generate(previous=previous, messages=batch)
            self._validate_content(content, batch)
            now = time.time()
            checkpoint_id = new_checkpoint_id()
            source_hash = hashlib.sha256(json.dumps({
                "previous_checkpoint_id": (
                    previous.checkpoint_id if previous else None),
                "messages": [
                    {"event_id": item.event_id, "sequence": item.sequence,
                     "role": item.role, "content": item.content}
                    for item in batch
                ],
            }, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            checkpoint = ContextCheckpoint(
                checkpoint_id=checkpoint_id,
                session_id=session_id,
                runtime=self.runtime,
                source_from_sequence=cursor + 1,
                covered_through_sequence=batch[-1].sequence,
                source_hash=source_hash,
                created_at=now,
                provider=self.generator.provider,
                model=self.generator.model,
                content=content,
            )
            segment = RuntimeContextSegment(
                segment_id=new_segment_id(),
                session_id=session_id,
                runtime=self.runtime,
                start_sequence=cursor + 1,
                end_sequence=batch[-1].sequence,
                checkpoint_id=checkpoint_id,
                created_at=now,
            )
            await self.store.append_checkpoint(checkpoint, segment)
            await self.store.append_event(ProductEvent(
                sequence=0,
                event_id=new_event_id(),
                type="context.checkpoint.created",
                session_id=session_id,
                occurred_at=now,
                runtime=self.runtime,
                data={
                    "checkpoint_id": checkpoint_id,
                    "segment_id": segment.segment_id,
                    "source_from_sequence": checkpoint.source_from_sequence,
                    "covered_through_sequence": checkpoint.covered_through_sequence,
                    "source_hash": source_hash,
                    "provider": checkpoint.provider,
                    "model": checkpoint.model,
                },
            ))
            self.log(
                "context_checkpoint_created", session_id=session_id,
                checkpoint_id=checkpoint_id,
                source_from_sequence=checkpoint.source_from_sequence,
                covered_through_sequence=checkpoint.covered_through_sequence,
                message_count=len(batch), provider=checkpoint.provider,
                model=checkpoint.model)
            for sink in self.checkpoint_sinks:
                await sink.checkpoint_created(checkpoint, segment)

    def _bounded_prefix(self, messages):
        batch, used = [], 0
        for message in messages:
            size = len(message.content)
            if batch and used + size > self.source_character_limit:
                break
            batch.append(message)
            used += size
        return tuple(batch)

    @staticmethod
    def _validate_content(content, messages=()):
        if not isinstance(content, dict):
            raise ValueError("Checkpoint Generator 必须返回 JSON object")
        required = {"summary", "user_facts", "decisions", "outcomes",
                    "open_loops", "entities", "commitments", "time_range",
                    "salient_events"}
        if not required <= set(content):
            raise ValueError("Checkpoint 缺少必要字段")
        if not isinstance(content["summary"], str) or not content["summary"].strip():
            raise ValueError("Checkpoint summary 不能为空")
        for field in ("user_facts", "decisions", "outcomes", "open_loops",
                      "entities", "commitments"):
            if not isinstance(content[field], list) \
                    or not all(isinstance(item, str) for item in content[field]):
                raise ValueError(f"Checkpoint {field} 必须是字符串数组")
        if not isinstance(content["time_range"], dict) \
                or set(content["time_range"]) != {"start", "end"}:
            raise ValueError("Checkpoint time_range 格式无效")
        if not isinstance(content["salient_events"], list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("event_id"), str)
                or not isinstance(item.get("description"), str)
                for item in content["salient_events"]):
            raise ValueError("Checkpoint salient_events 格式无效")
        allowed_ids = {item.event_id for item in messages}
        if allowed_ids and any(item["event_id"] not in allowed_ids
                               for item in content["salient_events"]):
            raise ValueError("Checkpoint salient_events 引用了范围外的 Event")

    async def drain(self):
        while self.tasks:
            await asyncio.gather(*tuple(self.tasks), return_exceptions=True)

    async def close(self):
        self.closed = True
        for task in tuple(self.tasks):
            task.cancel()
        await self.drain()
