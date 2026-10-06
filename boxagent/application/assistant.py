"""Application lifecycle owner and feature composition boundary."""

import asyncio
import json

from boxagent.core.events import StateEvents
from boxagent.domain.conversation.service import ConversationService
from boxagent.domain.memory.contracts import MemoryUnavailable
from boxagent.domain.memory.service import MemoryService
from boxagent.domain.perception.service import PerceptionService
from boxagent.domain.execution.service import ExecutionService
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.notification import NotificationOutbox


class BoxAgentApplication:
    """Own feature services and expose commands consumed by the native host."""

    def __init__(self, publish, executor_factory, voice_factory, memory_backend=None,
                 conversation_service=None, runtime_resources=(),
                 voice_history_builder=None, skill_service=None,
                 notification_outbox=None, memory_context_provider=None,
                 memory_ledger=None, memory_service=None):
        self.events = StateEvents(publish)
        self.conversation_service = conversation_service or ConversationService()
        self.notification_outbox = notification_outbox or NotificationOutbox()
        self.execution_service = ExecutionService(
            self.events, executor_factory, conversation=self.conversation_service,
            on_terminal=self._task_terminal)
        self.memory_service = memory_service or MemoryService(
            memory_backend, memory_ledger)
        self.skill_service = skill_service
        self.perception_service = PerceptionService(self.events)
        self.interaction_service = InteractionService(
            self.events, voice_factory, self.handle_tool,
            conversation=self.conversation_service,
            history_builder=voice_history_builder,
            notification_event=self._voice_notification_event,
            memory_context_provider=memory_context_provider)
        self.runtime_resources = tuple(runtime_resources)
        self._started = False
        self._closed = False

    async def start(self):
        if self._started:
            return
        session = await self.conversation_service.start()
        await self.notification_outbox.start()
        if session:
            self.events.emit("session.ready", session_id=session.session_id,
                             session_title=session.title)
        for resource in self.runtime_resources:
            await resource.start()
        self._started = True

    @property
    def state(self):
        return self.events.state

    @property
    def job(self):
        return self.execution_service.job

    @property
    def executor(self):
        return self.execution_service.executor

    @property
    def last_result(self):
        return self.execution_service.last_result

    @property
    def voice(self):
        return self.interaction_service.voice

    @property
    def voice_task(self):
        return self.interaction_service.task

    @property
    def observer(self):
        return self.perception_service.observer

    @observer.setter
    def observer(self, value):
        self.perception_service.observer = value

    def emit(self, kind, **changes):
        self.events.emit(kind, **changes)

    async def toggle_context(self):
        await self.perception_service.toggle()

    async def toggle_voice(self):
        await self.interaction_service.toggle()
        await self.deliver_pending_notifications()

    async def start_task(self, goal, *, from_text=False):
        return await self.execution_service.start(goal, from_text=from_text)

    async def submit_text(self, goal):
        if self.interaction_service.voice_factory is None:
            return await self.execution_service.submit_text(goal)
        result = await self.interaction_service.submit_text(goal)
        await self.deliver_pending_notifications()
        return result

    async def list_notifications(self, *, pending_only=True):
        if pending_only:
            items = await self.notification_outbox.pending()
        elif self.notification_outbox.repository:
            items = self.notification_outbox.repository.list()
        else:
            items = []
        return [item.payload() for item in items]

    async def acknowledge_notification(self, notification_id, channel,
                                       receipt="presented"):
        item = await self.notification_outbox.acknowledge(
            notification_id, channel, receipt)
        changes = {}
        if self.state.notification_id == notification_id:
            changes = {"notification_unread": False}
        self.events.emit("notification.delivered", **changes)
        return item.payload()

    async def deliver_pending_notifications(self):
        for item in await self.notification_outbox.pending():
            if item.status == "delivering":
                continue
            if await self._deliver_voice_notification(item):
                continue
            self._publish_system_notification(item)

    async def list_sessions(self, *, include_archived=False):
        sessions = await self.conversation_service.list_sessions(
            include_archived=include_archived)
        return [item.payload() for item in sessions]

    async def create_session(self, title="新会话"):
        self._require_idle_session_switch()
        session = await self.conversation_service.create_session(title)
        self.events.emit("session.created", session_id=session.session_id,
                         session_title=session.title, user_text="", assistant_text="")
        return session.payload()

    async def activate_session(self, session_id):
        self._require_idle_session_switch()
        session = await self.conversation_service.activate_session(session_id)
        messages = await self.conversation_service.recent_messages(session.session_id)
        user_text = next((item.content for item in reversed(messages)
                          if item.role == "user"), "")
        assistant_text = next((item.content for item in reversed(messages)
                               if item.role == "assistant"), "")
        self.events.emit("session.activated", session_id=session.session_id,
                         session_title=session.title, user_text=user_text,
                         assistant_text=assistant_text)
        return session.payload()

    async def archive_session(self, session_id):
        self._require_idle_session_switch()
        archived = await self.conversation_service.archive_session(session_id)
        active = await self.conversation_service.active_session()
        if active is None:
            active = await self.conversation_service.create_session()
        self.events.emit("session.archived", session_id=active.session_id,
                         session_title=active.title, user_text="", assistant_text="")
        return archived.payload()

    async def session_events(self, session_id, *, after_sequence=0, limit=None):
        events = await self.conversation_service.events(
            session_id, after_sequence=after_sequence, limit=limit)
        return [item.payload() for item in events]

    def _require_idle_session_switch(self):
        if self.execution_service.job and not self.execution_service.job.done():
            raise RuntimeError("任务执行期间不能切换 Session；请等待完成或先停止任务")

    async def run_job(self, goal):
        return await self.execution_service._run(goal)

    def progress(self, text):
        self.execution_service.progress(text)

    async def approve(self, params):
        return await self.execution_service.approve(params)

    async def answer_approval(self, allowed):
        await self.execution_service.answer_approval(allowed)

    async def cancel_task(self):
        await self.execution_service.cancel()

    async def stop_voice(self):
        await self.interaction_service.stop()

    async def remember_memory(self, content, *, source="explicit"):
        return await self.memory_service.remember(content, source=source)

    async def recall_memory(self, query, *, top_k=5):
        return await self.memory_service.recall(query, top_k=top_k)

    async def memory_snapshot(self, **arguments):
        return await self.memory_service.snapshot(**arguments)

    async def pending_memories(self):
        return await self.memory_service.pending_review()

    async def approve_memory(self, memory_id):
        return await self.memory_service.approve(memory_id)

    async def reject_memory(self, memory_id):
        return await self.memory_service.reject(memory_id)

    async def retry_memory_index(self, memory_id):
        return await self.memory_service.retry_index(memory_id)

    async def delete_memory_node(self, memory_id):
        return await self.memory_service.delete_node(memory_id)

    async def list_skills(self):
        if self.skill_service is None:
            return []
        return [item.payload() for item in self.skill_service.list()]

    async def create_skill(self, **params):
        service = self._require_skill_service()
        item = service.create(
            params.get("skill_id", ""), name=params.get("name", ""),
            description=params.get("description", ""),
            instructions=params.get("instructions", ""))
        await self._sync_runtime_skills()
        return item.payload()

    async def update_skill(self, **params):
        service = self._require_skill_service()
        item = service.update(
            params.get("skill_id", ""), name=params.get("name", ""),
            description=params.get("description", ""),
            instructions=params.get("instructions", ""))
        await self._sync_runtime_skills()
        return item.payload()

    async def set_skill_enabled(self, skill_id, enabled):
        service = self._require_skill_service()
        item = service.set_enabled(skill_id, enabled)
        await self._sync_runtime_skills()
        return item.payload()

    async def delete_skill(self, skill_id):
        service = self._require_skill_service()
        service.delete(skill_id)
        await self._sync_runtime_skills()
        return {"status": "succeeded", "skill_id": skill_id}

    def _require_skill_service(self):
        if self.skill_service is None:
            raise RuntimeError("Skill 管理服务未启用")
        return self.skill_service

    async def _sync_runtime_skills(self):
        for resource in self.runtime_resources:
            sync = getattr(resource, "sync_skills", None)
            if sync:
                await sync()

    async def forget_memory(self, memory_ids):
        return await self.memory_service.forget(memory_ids)

    async def handle_tool(self, name, arguments, *, interaction=None):
        try:
            data = json.loads(arguments) if isinstance(arguments, str) else arguments
            if name == "run_task":
                # The persisted User Final is authoritative. Qwen only decides
                # whether to delegate; it must not rewrite the request that
                # becomes the Codex user turn. Keep the argument as a fallback
                # for direct/internal callers without a Product Interaction.
                query = (interaction.user_event.content
                         if interaction and interaction.user_event.content
                         else data.get("goal", ""))
                accepted = await self.execution_service.start(
                    query, interaction=interaction)
                return accepted
            if name == "cancel_task":
                await self.execution_service.cancel()
                return self.execution_service.last_result
            if name == "task_status":
                return {**self.execution_service.last_result, "progress": self.state.task_text}
            if name == "remember_memory":
                return await self.memory_service.remember(
                    data["content"], source="voice_explicit",
                    session_id=(interaction.session.session_id if interaction else ""),
                    interaction_id=(interaction.interaction_id if interaction else ""),
                    source_event_ids=((interaction.user_event.event_id,)
                                      if interaction else ()))
            if name == "recall_memory":
                return await self.memory_service.recall(data["query"])
            if name == "forget_memory":
                return await self.memory_service.forget(data["memory_ids"])
            return {"status": "failed", "message": "未知的委托入口。"}
        except (MemoryUnavailable, ValueError, KeyError, TypeError, SyntaxError) as exc:
            return {"status": "failed", "message": str(exc)}

    async def _task_terminal(self, *, interaction, task_id, outcome, summary):
        if interaction is None:
            return
        item = await self.notification_outbox.enqueue(
            session_id=interaction.session.session_id,
            interaction_id=interaction.interaction_id,
            task_id=task_id, outcome=outcome, summary=summary)
        if item is None or item.status == "delivered":
            return
        self.events.emit(
            "notification.pending", notification_id=item.notification_id,
            notification_text=item.summary, notification_unread=True)
        if not await self._deliver_voice_notification(item):
            self._publish_system_notification(item)

    async def _deliver_voice_notification(self, item):
        if not self.interaction_service.is_connected:
            return False
        await self.notification_outbox.claim(item.notification_id, "voice")
        accepted = await self.interaction_service.deliver_notification(item.payload())
        if accepted:
            return True
        await self.notification_outbox.retry(
            item.notification_id, "voice", "realtime_unavailable")
        return False

    def _publish_system_notification(self, item):
        self.events.emit(
            "notification.system_requested",
            notification_id=item.notification_id,
            notification_text=item.summary,
            notification_unread=True)

    async def _voice_notification_event(self, kind, payload):
        notification_id = payload.get("notification_id", "")
        if not notification_id:
            return
        if kind == "playback_started":
            await self.acknowledge_notification(
                notification_id, "voice", "playback_started")
        elif kind == "delivery_interrupted":
            item = await self.notification_outbox.retry(
                notification_id, "voice", payload.get("error", "connection_closed"))
            self._publish_system_notification(item)

    async def close(self):
        if self._closed:
            return
        await self.perception_service.close()
        await self.execution_service.close()
        await self.interaction_service.close()
        for resource in reversed(self.runtime_resources):
            await resource.close()
        await self.memory_service.close()
        self._closed = True
