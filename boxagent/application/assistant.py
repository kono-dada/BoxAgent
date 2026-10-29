"""Application lifecycle owner and feature composition boundary."""

import asyncio
import json
from uuid import uuid4
import logging

logger = logging.getLogger(__name__)

from boxagent.core.events import StateEvents
from boxagent.application.memory import DisabledMemoryModule
from boxagent.application.skill_authoring import (
    has_explicit_skill_authorization,
    is_pending_skill_confirmation,
)
from boxagent.domain.skill import SkillDraftStatus
from boxagent.domain.context import project_context
from boxagent.domain.conversation.service import ConversationService
from boxagent.domain.memory.contracts import MemoryUnavailable
from boxagent.domain.perception.service import PerceptionService
from boxagent.domain.execution.service import ExecutionService
from boxagent.domain.interaction.service import InteractionService
from boxagent.domain.interaction.delegation import delegation_exclusion
from boxagent.domain.notification import NotificationOutbox


class BoxAgentApplication:
    """Own feature services and expose commands consumed by the native host."""

    def __init__(self, publish, executor_factory, voice_factory, *, memory=None,
                 conversation_service=None, runtime_resources=(),
                 voice_history_builder=None, skill_service=None,
                 skill_authoring=None, task_trace_reader=None,
                 notification_outbox=None, context_ledger=None,
                 context_observer=None, context_history=None, turn_preparer=None):
        self.events = StateEvents(publish)
        self.conversation_service = conversation_service or ConversationService()
        self.notification_outbox = notification_outbox or NotificationOutbox()
        self.execution_service = ExecutionService(
            self.events, executor_factory, conversation=self.conversation_service,
            on_terminal=self._task_terminal)
        self.memory = memory or DisabledMemoryModule()
        self.skill_service = skill_service
        self.skill_authoring = skill_authoring
        self.task_trace_reader = task_trace_reader
        self.context_ledger = context_ledger
        self.context_observer = context_observer
        self.context_history = context_history
        self.context_cache = {}
        self.perception_service = PerceptionService(self.events)
        self.interaction_service = InteractionService(
            self.events, voice_factory, self.handle_tool,
            conversation=self.conversation_service,
            history_builder=voice_history_builder,
            notification_event=self._voice_notification_event,
            memory=self.memory,
            turn_preparer=turn_preparer,
            context_observer=context_observer)
        self.runtime_resources = tuple(runtime_resources)
        self.skill_jobs = set()
        self._started = False
        self._closed = False

    async def start(self):
        if self._started:
            return
        session = await self.conversation_service.start()
        await self.notification_outbox.start()
        if self.skill_authoring is not None:
            self.skill_authoring.recover_interrupted()
        if session:
            self.events.emit("session.ready", session_id=session.session_id,
                             session_title=session.title)
        for resource in self.runtime_resources:
            await resource.start()
        await self.memory.start()
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
        confirmation = await self._pending_skill_confirmation(goal)
        if confirmation is not None:
            return confirmation
        result = await self.interaction_service.submit_text(goal)
        await self.deliver_pending_notifications()
        return result

    async def _pending_skill_confirmation(self, goal):
        if (self.skill_authoring is None
                or not is_pending_skill_confirmation(goal)):
            return None
        session = await self.conversation_service.active_session()
        if session is None:
            return None
        draft = self.skill_authoring.latest(
            session_id=session.session_id,
            statuses=(SkillDraftStatus.PENDING, SkillDraftStatus.PREPARING))
        if draft is None:
            return None

        async def install(interaction):
            if draft.status == SkillDraftStatus.PREPARING:
                return "Skill 草稿还在后台生成，完成后我会主动提醒你。"
            result = await self.install_skill_draft("", interaction=interaction)
            return result.get("message", "Skill 已安装。")

        return await self.interaction_service.submit_host_text(goal, install)

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
        await self.interaction_service.reset_session()
        session = await self.conversation_service.create_session(title)
        self.events.emit("session.created", **self._session_state(
            session.session_id, session.title))
        return session.payload()

    async def activate_session(self, session_id):
        self._require_idle_session_switch()
        await self.interaction_service.reset_session()
        session = await self.conversation_service.activate_session(session_id)
        messages = await self.conversation_service.recent_messages(session.session_id)
        user_text = next((item.content for item in reversed(messages)
                          if item.role == "user"), "")
        assistant_text = next((item.content for item in reversed(messages)
                               if item.role == "assistant"), "")
        self.events.emit("session.activated", **self._session_state(
            session.session_id, session.title,
            user_text=user_text, assistant_text=assistant_text))
        return session.payload()

    async def archive_session(self, session_id):
        self._require_idle_session_switch()
        await self.interaction_service.reset_session()
        archived = await self.conversation_service.archive_session(session_id)
        active = await self.conversation_service.active_session()
        if active is None:
            active = await self.conversation_service.create_session()
        messages = await self.conversation_service.recent_messages(active.session_id)
        user_text = next((item.content for item in reversed(messages)
                          if item.role == "user"), "")
        assistant_text = next((item.content for item in reversed(messages)
                               if item.role == "assistant"), "")
        self.events.emit("session.archived", **self._session_state(
            active.session_id, active.title,
            user_text=user_text, assistant_text=assistant_text))
        return archived.payload()

    async def session_events(self, session_id, *, after_sequence=0, limit=None):
        events = await self.conversation_service.events(
            session_id, after_sequence=after_sequence, limit=limit)
        return [item.payload() for item in events]

    async def session_timeline(self, session_id: str, *, after_seq: int = 0,
                               limit: int = 150) -> list[dict]:
        stream = getattr(self.conversation_service.store, "events", None)
        if stream is None:
            return await self.session_events(session_id, after_sequence=after_seq, limit=limit)
        rows = await asyncio.to_thread(stream.read, session_id, after_seq=after_seq)
        return [row.payload() for row in rows[-max(1, min(limit, 500)):]]

    def _require_idle_session_switch(self):
        if self.execution_service.job and not self.execution_service.job.done():
            raise RuntimeError("任务执行期间不能切换 Session；请等待完成或先停止任务")

    @staticmethod
    def _session_state(session_id, session_title, *, user_text="",
                       assistant_text=""):
        """Replace every Session-owned projection when the active Session changes."""
        return {
            "session_id": session_id,
            "session_title": session_title,
            "task": "idle",
            "task_id": "",
            "task_started_at": 0,
            "task_ended_at": 0,
            "task_activity_at": 0,
            "task_text": "",
            "user_text": user_text,
            "assistant_text": assistant_text,
            "approval": "",
            "error": "",
            "notification_id": "",
            "notification_text": "",
            "notification_unread": False,
        }

    async def run_job(self, goal):
        return await self.execution_service._run(goal)

    def progress(self, text):
        self.execution_service.progress(text)

    async def approve(self, params):
        return await self.execution_service.approve(params)

    async def answer_approval(self, allowed):
        await self.execution_service.answer_approval(allowed)

    async def cancel_task(self) -> dict[str, object]:
        return await self.execution_service.cancel()

    async def cancel_all_tasks(self) -> dict[str, object]:
        return await self.execution_service.cancel_all()

    async def stop_voice(self):
        await self.interaction_service.stop()

    async def remember_memory(self, content, *, source="explicit"):
        return await self.memory.remember(content, source=source)

    async def recall_memory(self, query, *, top_k=5):
        return await self.memory.recall(query, top_k=top_k)

    async def _context_events(self, session_id):
        """Prefer the live ledger; fall back to trace reconstruction for old Sessions."""
        events = (await asyncio.to_thread(self.context_ledger.read, session_id)
                  if self.context_ledger is not None else [])
        if events or self.context_history is None:
            return events, "ledger"
        return (await asyncio.to_thread(self.context_history.read, session_id),
                "trace_replay")

    async def context_snapshot(self, session_id=""):
        """Project the Session Context Ledger for the admin Context viewer."""
        if not session_id:
            active = await self.conversation_service.active_session()
            session_id = active.session_id if active else ""
        if not session_id:
            return {"status": "empty", "session_id": "",
                    "message": "当前没有活动 Session"}
        if self.context_ledger is None:
            return {"status": "unavailable", "session_id": session_id,
                    "message": "Context Ledger 未启用"}
        try:
            events, source = await self._context_events(session_id)
        except ValueError as exc:
            return {"status": "corrupt", "session_id": session_id,
                    "message": str(exc)}
        title = ""
        for item in await self.conversation_service.list_sessions(include_archived=True):
            if item.session_id == session_id:
                title = item.title
        if not events:
            return {"status": "empty", "session_id": session_id,
                    "session_title": title,
                    "message": f"「{title or session_id}」还没有发给 Qwen 的上下文；"
                               "开启语音或发一句文字后点刷新"}
        key = (len(events), events[-1].seq, events[-1].time)
        cached = self.context_cache.get(session_id)
        if cached is None or cached[0] != key:
            cached = (key, project_context(session_id, events))
            self.context_cache[session_id] = cached
        return {"status": "ready", "session_title": title, "source": source, **cached[1]}

    async def context_entry(self, session_id, seq):
        """Return the full model-visible text of one ledger entry."""
        if self.context_ledger is None or not session_id:
            return {"status": "unavailable", "content": ""}
        events, _source = await self._context_events(session_id)
        for event in events:
            if event.seq == int(seq):
                return {"status": "ready", "seq": event.seq,
                        "content": str(event.data.get("content") or "")}
        return {"status": "missing", "content": ""}

    async def memory_snapshot(self, **arguments):
        return await self.memory.snapshot(**arguments)

    async def delete_memory_node(self, memory_id):
        return await self.memory.delete_node(memory_id)

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

    async def find_skills(self, query, *, limit=8):
        service = self._require_skill_authoring()
        return {"status": "succeeded", "skills": service.find(query, limit=limit)}

    async def prepare_skill_draft(self, request, *, interaction=None):
        service = self._require_skill_authoring()
        if self.execution_service.job and not self.execution_service.job.done():
            return {"status": "busy", "message": "请等待当前任务结束后再沉淀 Skill。"}
        session_id = interaction.session.session_id if interaction else ""
        interaction_id = interaction.interaction_id if interaction else ""
        task_id = ""
        if session_id and self.conversation_service.store:
            task_id = await self.conversation_service.latest_task_id(
                session_id, exclude_interaction_id=interaction_id)
        if not task_id:
            task_id = str(self.execution_service.last_result.get("task_id") or "")
        task_context = (self.task_trace_reader.read(task_id)
                        if self.task_trace_reader and task_id else
                        dict(self.execution_service.last_result))
        draft = service.begin(
            request, session_id=session_id, interaction_id=interaction_id)
        job = asyncio.create_task(self._run_skill_draft_job(
            draft.draft_id, request=request, session_id=session_id,
            interaction_id=interaction_id, task_context=task_context))
        self.skill_jobs.add(job)
        job.add_done_callback(self.skill_jobs.discard)
        self.events.emit("skill.draft.preparing")
        return {
            "status": "accepted",
            "draft_id": draft.draft_id,
            "message": "Skill 草稿已在后台生成；完成后会主动提醒用户。",
        }

    async def _run_skill_draft_job(self, draft_id, *, request, session_id,
                                   interaction_id, task_context):
        authoring = self.skill_authoring
        try:
            if authoring is None:
                raise RuntimeError("Skill 草稿服务未配置")
            result = await authoring.prepare(
                request, session_id=session_id,
                interaction_id=interaction_id, task_context=task_context,
                draft_id=draft_id)
            if result["status"] == "preview":
                name = result["draft"].get("name") or result["draft"].get("skill_id")
                summary = f"Skill 草稿“{name}”已生成，回复“安装”即可应用。"
                outcome = "draft_ready"
                self.events.emit("skill.draft.ready")
            else:
                summary = result.get("message", "现有 Skill 已能覆盖该操作。")
                outcome = "not_needed"
                self.events.emit("skill.draft.not_needed")
        except Exception as exc:
            if authoring is not None:
                authoring.fail(draft_id, str(exc))
            summary = f"Skill 草稿生成失败：{str(exc) or type(exc).__name__}"
            outcome = "failed"
            self.events.emit("skill.draft.failed")
        await self._publish_background_result(
            session_id=session_id, interaction_id=interaction_id,
            operation_id=draft_id, outcome=outcome, summary=summary)

    async def install_skill_draft(self, draft_id, *, interaction=None):
        service = self._require_skill_authoring()
        authorization = (interaction.user_event.content if interaction else "")
        if not has_explicit_skill_authorization(authorization):
            return {
                "status": "confirmation_required",
                "message": "安装或更新 Skill 会写入本地配置，请先让用户明确确认。",
                "draft_id": draft_id,
            }
        result = service.install(
            draft_id,
            session_id=(interaction.session.session_id if interaction else ""))
        await self._sync_runtime_skills()
        return result

    def _require_skill_service(self):
        if self.skill_service is None:
            raise RuntimeError("Skill 管理服务未启用")
        return self.skill_service

    def _require_skill_authoring(self):
        if self.skill_authoring is None:
            raise RuntimeError("Skill 自动创建服务未启用")
        return self.skill_authoring

    async def _sync_runtime_skills(self):
        for resource in self.runtime_resources:
            sync = getattr(resource, "sync_skills", None)
            if sync:
                await sync()

    async def forget_memory(self, memory_ids):
        return await self.memory.forget(memory_ids)

    def request_avatar_action(self, action: object) -> dict[str, str]:
        """Publish a validated semantic request; playback remains a UI concern."""
        if not isinstance(action, str) or action not in {"dance", "happy", "laugh", "shy"}:
            return {"status": "failed", "message": "不支持的桌宠动作"}
        self.events.emit("avatar.action", avatar_action=action,
                         avatar_action_id=uuid4().hex)
        return {"status": "requested", "action": action,
                "message": "已请求桌宠播放；界面尚未确认播放完成。"}

    async def handle_tool(self, name, arguments, *, interaction=None):
        try:
            data = json.loads(arguments) if isinstance(arguments, str) else arguments
            if name == "play_avatar_action":
                action = data.get("action") if isinstance(data, dict) else None
                return self.request_avatar_action(action)
            if name == "run_task":
                # The persisted User Final is authoritative. Qwen only decides
                # whether to delegate; it must not rewrite the request that
                # becomes the Codex user turn. Keep the argument as a fallback
                # for direct/internal callers without a Product Interaction.
                query = (interaction.user_event.content
                         if interaction and interaction.user_event.content
                         else data.get("goal", ""))
                exclusion = delegation_exclusion(query) if isinstance(query, str) else None
                if exclusion is not None:
                    return {
                        "status": "not_delegated", "reason": exclusion,
                        "message": ("当前是进度询问，未创建新任务。请调用 task_status 查看真实进度后自然回答。"
                                    if exclusion == "status" else
                                    "当前是聊天或未明确的请求，未创建新任务。请自然回应或询问用户想做什么。"),
                    }
                accepted = await self.execution_service.start(
                    query, interaction=interaction)
                return accepted
            if name == "cancel_task":
                return await self.cancel_task()
            if name == "cancel_all_tasks":
                return await self.cancel_all_tasks()
            if name == "task_status":
                return {**self.execution_service.last_result, "progress": self.state.task_text,
                        "task_queue": self.state.task_queue}
            if name == "remember_memory":
                return await self.memory.remember(
                    data["content"], source="voice_explicit",
                    session_id=(interaction.session.session_id if interaction else ""),
                    interaction_id=(interaction.interaction_id if interaction else ""),
                    source_event_ids=((interaction.user_event.event_id,)
                                      if interaction else ()))
            if name == "recall_memory":
                result = await self.memory.recall(data["query"])
                return {key: result[key] for key in (
                    "status", "message", "memories") if key in result}
            if name == "forget_memory":
                return await self.memory.forget(data["memory_ids"])
            if name == "find_skill":
                return await self.find_skills(
                    data.get("query", ""), limit=int(data.get("limit", 8)))
            if name == "prepare_skill":
                request = (interaction.user_event.content
                           if interaction and interaction.user_event.content
                           else data.get("request", ""))
                return await self.prepare_skill_draft(
                    request, interaction=interaction)
            if name == "install_skill_draft":
                return await self.install_skill_draft(
                    data.get("draft_id", ""), interaction=interaction)
            return {"status": "failed", "message": "未知的委托入口。"}
        except (MemoryUnavailable, OSError, ValueError, KeyError, TypeError,
                SyntaxError) as exc:
            return {"status": "failed", "message": str(exc)}

    async def _publish_background_result(self, *, session_id, interaction_id,
                                         operation_id, outcome, summary):
        item = await self.notification_outbox.enqueue(
            session_id=session_id, interaction_id=interaction_id,
            task_id=operation_id, outcome=outcome, summary=summary)
        if item is None or item.status == "delivered":
            return
        self.events.emit(
            "notification.pending", notification_id=item.notification_id,
            notification_text=item.summary, notification_unread=True)
        if not await self._deliver_voice_notification(item):
            self._publish_system_notification(item)

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
        for job in tuple(self.skill_jobs):
            job.cancel()
        if self.skill_jobs:
            await asyncio.gather(*self.skill_jobs, return_exceptions=True)
        self.skill_jobs.clear()
        await self.interaction_service.close()
        logger.info("closing memory")
        await self.memory.close()
        logger.info("memory closed")
        if self.context_observer is not None and hasattr(self.context_observer, "flush"):
            await asyncio.to_thread(self.context_observer.flush)
        for resource in reversed(self.runtime_resources):
            logger.info("closing resource %s", type(resource).__name__)
            await resource.close()
        self._closed = True
