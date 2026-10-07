"""Product Session lifecycle and append-only conversation events."""

import hashlib
import time
from dataclasses import replace

from boxagent.core.ids import new_event_id, new_interaction_id
from boxagent.domain.conversation.contracts import NullContextCheckpointSink
from boxagent.domain.conversation.models import InteractionContext, ProductEvent, RuntimeBinding


class ConversationService:
    def __init__(self, store=None, *, message_sinks=(), finalization_sinks=(),
                 checkpoint_sink=None):
        self.store = store
        self.message_sinks = tuple(message_sinks)
        self.finalization_sinks = tuple(finalization_sinks)
        self.checkpoint_sink = checkpoint_sink or NullContextCheckpointSink()

    async def start(self):
        if self.store is None:
            return None
        await self.store.start()
        await self._recover_interrupted_interactions()
        session = await self.store.active_session()
        return session or await self.store.create_session()

    async def create_session(self, title="新会话"):
        self._require_store()
        return await self.store.create_session(title)

    async def list_sessions(self, *, include_archived=False):
        if self.store is None:
            return []
        return await self.store.list_sessions(include_archived=include_archived)

    async def active_session(self):
        if self.store is None:
            return None
        return await self.store.active_session()

    async def activate_session(self, session_id):
        self._require_store()
        return await self.store.activate_session(session_id)

    async def archive_session(self, session_id):
        self._require_store()
        return await self.store.archive_session(session_id)

    async def events(self, session_id, *, after_sequence=0, limit=None):
        self._require_store()
        return await self.store.read_events(
            session_id, after_sequence=after_sequence, limit=limit)

    async def begin_interaction(self, content, *, task_id=None,
                                source="text", runtime="codex"):
        self._require_store()
        session = await self.store.active_session() or await self.store.create_session()
        interaction_id = new_interaction_id()
        occurred_at = time.time()
        await self.store.append_event(ProductEvent(
            sequence=0, event_id=new_event_id(), type="interaction.started",
            session_id=session.session_id, interaction_id=interaction_id,
            task_id=task_id, runtime=runtime, source=source, status="running",
            occurred_at=occurred_at))
        user_event = await self.store.append_event(ProductEvent(
            sequence=0, event_id=new_event_id(), type="message.final",
            session_id=session.session_id, interaction_id=interaction_id,
            task_id=task_id, runtime=runtime, role="user", content=content,
            source=source, occurred_at=occurred_at))
        source_hash = hashlib.sha256(
            f"{user_event.event_id}:{user_event.content}".encode()).hexdigest()
        for sink in self.message_sinks:
            await sink.user_message_committed(
                session_id=session.session_id,
                interaction_id=interaction_id,
                event=user_event,
                source_hash=source_hash,
            )
        if session.title == "新会话":
            session = await self.store.rename_session(
                session.session_id, self._title_from(content))
        prior = tuple(event for event in await self.recent_messages(session.session_id)
                      if event.interaction_id != interaction_id)
        binding = await self.store.runtime_binding(session.session_id, runtime)
        return InteractionContext(session, interaction_id, user_event, prior, binding)

    async def finish_interaction(self, context: InteractionContext, *, status: str,
                                 assistant_content: str = "", runtime="codex",
                                 data=None, task_id=None):
        self._require_store()
        if assistant_content:
            await self.append_assistant_message(
                context, assistant_content, runtime=runtime, task_id=task_id)
        terminal = await self.store.append_event(ProductEvent(
            sequence=0, event_id=new_event_id(), type="interaction.finalized",
            session_id=context.session.session_id,
            interaction_id=context.interaction_id,
            task_id=task_id or context.user_event.task_id,
            runtime=runtime, status=status,
            occurred_at=time.time(), data=dict(data or {})))
        binding = await self.store.runtime_binding(context.session.session_id, runtime)
        if binding:
            await self.store.save_runtime_binding(
                context.session.session_id,
                RuntimeBinding(
                    runtime=binding.runtime, provider=binding.provider,
                    model=binding.model, thread_id=binding.thread_id,
                    context_cursor=terminal.sequence,
                    previous_threads=binding.previous_threads))
        source_hash = hashlib.sha256(
            f"{context.user_event.event_id}:{terminal.event_id}".encode()).hexdigest()
        for sink in self.finalization_sinks:
            await sink.interaction_finalized(
                session_id=context.session.session_id,
                interaction_id=context.interaction_id,
                source_hash=source_hash)
        await self.checkpoint_sink.interaction_finalized(
            session_id=context.session.session_id,
            interaction_id=context.interaction_id,
            source_hash=source_hash)
        return terminal

    async def append_assistant_message(self, context: InteractionContext, content: str,
                                       *, runtime: str, task_id=None):
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Assistant Final Message 不能为空")
        return await self.store.append_event(ProductEvent(
            sequence=0, event_id=new_event_id(), type="message.final",
            session_id=context.session.session_id,
            interaction_id=context.interaction_id,
            task_id=task_id or context.user_event.task_id, runtime=runtime,
            role="assistant", content=content.strip(), source=runtime,
            occurred_at=time.time()))

    async def for_runtime(self, context: InteractionContext, runtime: str):
        binding = await self.store.runtime_binding(context.session.session_id, runtime)
        return replace(context, runtime_binding=binding)

    async def _recover_interrupted_interactions(self):
        sessions = await self.store.list_sessions(include_archived=True)
        for session in sessions:
            events = await self.store.read_events(session.session_id)
            open_interactions = {}
            for event in events:
                if event.type == "interaction.started" and event.interaction_id:
                    open_interactions[event.interaction_id] = event
                elif event.type == "interaction.finalized" and event.interaction_id:
                    open_interactions.pop(event.interaction_id, None)
            for interaction_id, started in open_interactions.items():
                await self.store.append_event(ProductEvent(
                    sequence=0, event_id=new_event_id(), type="interaction.finalized",
                    session_id=session.session_id, interaction_id=interaction_id,
                    task_id=started.task_id, runtime=started.runtime,
                    source="engine_recovery", status="interrupted",
                    occurred_at=time.time(), data={"reason": "engine_restarted"}))

    async def recent_messages(self, session_id, *, after_sequence=0, limit=40):
        events = await self.store.read_events(
            session_id, after_sequence=after_sequence)
        messages = [event for event in events
                    if event.type == "message.final" and event.content]
        return messages[-limit:] if limit is not None else messages

    async def latest_checkpoint(self, session_id, runtime):
        self._require_store()
        return await self.store.latest_checkpoint(session_id, runtime)

    async def runtime_context(self, session_id, runtime, *, limit=None):
        """Return one portable checkpoint plus only messages created after it."""
        checkpoint = await self.latest_checkpoint(session_id, runtime)
        cursor = checkpoint.covered_through_sequence if checkpoint else 0
        messages = await self.recent_messages(
            session_id, after_sequence=cursor, limit=limit)
        return checkpoint, messages

    async def latest_task_id(self, session_id, *, exclude_interaction_id=""):
        """Resolve the latest durable task instead of process-local last_result."""
        events = await self.store.read_events(session_id)
        for event in reversed(events):
            if (event.type == "interaction.finalized" and event.task_id
                    and event.interaction_id != exclude_interaction_id):
                return event.task_id
        return ""

    async def bind_runtime(self, session_id, *, runtime, provider, model, thread_id):
        self._require_store()
        current = await self.store.runtime_binding(session_id, runtime)
        previous = list(current.previous_threads) if current else []
        if current and current.thread_id != thread_id:
            previous.append(current.thread_id)
        await self.store.save_runtime_binding(session_id, RuntimeBinding(
            runtime=runtime, provider=provider, model=model, thread_id=thread_id,
            context_cursor=current.context_cursor if current else 0,
            previous_threads=tuple(dict.fromkeys(previous))))

    @staticmethod
    def _title_from(content):
        title = " ".join(content.strip().split())
        return title[:24] + ("…" if len(title) > 24 else "") or "新会话"

    def _require_store(self):
        if self.store is None:
            raise RuntimeError("Conversation Store 未配置")
