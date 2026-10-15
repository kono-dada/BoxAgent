"""Voice lifecycle independent from the concrete realtime provider."""

import asyncio
import contextlib
import logging
import time

from boxagent.domain.context.contracts import NullContextObserver
from boxagent.domain.conversation.models import InteractionContext
from boxagent.domain.interaction.delegation import DecisionAdvice

logger = logging.getLogger(__name__)


def _is_idle_realtime_disconnect(message):
    text = str(message or "").lower()
    return "response_idle_timeout" in text or (
        "180 seconds" in text
        and any(marker in text for marker in (
            "no user input was received",
            "no response was generated",
        )))


def _is_reconnectable_voice_disconnect(message):
    text = str(message or "").lower()
    return "aoq" in text and any(marker in text for marker in (
        "连接已断开", "连接失败", "connection closed", "connection failed",
    ))


class InteractionService:
    def __init__(self, events, voice_factory, handle_tool, *, conversation=None,
                 history_builder=None, notification_event=None,
                 memory=None, reconnect_delays=(.5, 1.5, 3.0),
                 context_observer=None, turn_preparer=None):
        self.events = events
        self.context_observer = context_observer or NullContextObserver()
        self.voice_factory = voice_factory
        self.handle_tool = handle_tool
        self.conversation = conversation
        self.history_builder = history_builder
        self.notification_event = notification_event
        self.memory = memory
        self.turn_preparer = turn_preparer
        self.voice = self.task = None
        self.connection_microphone = False
        self.connection_error = None
        self.interaction = None
        self.response_interactions = {}
        self.tool_interactions = {}
        self.decisions: dict[str, DecisionAdvice] = {}
        self.pending_user_responses = []
        self.closed_interactions = set()
        self.toggle_lock = asyncio.Lock()
        self.text_lock = asyncio.Lock()
        self.voice_requested = False
        self.reconnect_delays = tuple(reconnect_delays)
        self.reconnect_task = None

    @property
    def is_connected(self):
        return bool(self.voice and self.task and not self.task.done())

    async def toggle(self):
        async with self.toggle_lock:
            if self.voice_requested or (
                    self.is_connected and self.connection_microphone):
                self.voice_requested = False
                self.events.emit("voice.stopping", voice="stopping",
                                 speaking=False, user_speaking=False)
                finish_pending = getattr(
                    self.voice, "finish_pending_input", None)
                if finish_pending is not None:
                    with contextlib.suppress(Exception):
                        await finish_pending()
                await self.stop()
                return
            if self.is_connected:
                await self.stop()
            self.voice_requested = True
            try:
                await self._connect(microphone=True)
            except BaseException:
                self.voice_requested = False
                raise

    async def submit_text(self, text):
        async with self.text_lock:
            return await self._submit_text(text)

    async def _submit_text(self, text):
        if not isinstance(text, str) or not text.strip():
            return {"status": "failed", "message": "请输入想说的话"}
        async with self.toggle_lock:
            await self._connect(microphone=False)
        if self.conversation and self.conversation.store:
            if self.interaction is not None:
                await self.conversation.finish_interaction(
                    self.interaction, status="interrupted", runtime="qwen_realtime")
                self._close_interaction(self.interaction)
            self.interaction = await self.conversation.begin_interaction(
                text, source="text", runtime="qwen_realtime")
            begin_query = getattr(self.voice, "begin_query", None)
            if begin_query is not None:
                begin_query(self.interaction.interaction_id)
            self.context_observer.turn_started(
                self.interaction.session.session_id, channel="text")
            # Publish the durable user turn before optional memory retrieval.
            # Recall may take hundreds of milliseconds, but it should not make
            # the text input appear unresponsive.
            self.events.emit(
                "front.user_text", user_text=text, assistant_text="", error="")
            if self.turn_preparer is None:
                await self._inject_memory_context(text, self.interaction)
        else:
            self.events.emit(
                "front.user_text", user_text=text, assistant_text="", error="")
        try:
            submit = getattr(self.voice, "submit_text", None)
            if submit is None:
                raise RuntimeError("当前前台 Runtime 不支持文字输入")
            if self.turn_preparer is not None and self.interaction is not None:
                context = self.interaction
                voice = self.voice
                packet, report = await self.turn_preparer.prepare(
                    text, session_id=context.session.session_id,
                    query_id=context.interaction_id, context=self._decision_context(context))
                if self.interaction is not context or self.voice is not voice:
                    return {"status": "interrupted", "message": "本轮已被新的输入打断"}
                self.context_observer.turn_prepared(context.session.session_id, **report)
                self._store_decision(context, report)
                await submit(text, reference=packet, query_id=context.interaction_id)
            else:
                await submit(text)
        except Exception:
            if self.interaction is not None and self.conversation is not None:
                await self.conversation.finish_interaction(
                    self.interaction, status="failed", runtime="qwen_realtime")
                self._close_interaction(self.interaction)
            raise
        result = {"status": "accepted"}
        if self.interaction is not None:
            result.update(session_id=self.interaction.session.session_id,
                          interaction_id=self.interaction.interaction_id)
        return result

    async def submit_host_text(self, text, handler, *, runtime="boxagent_host"):
        """Complete deterministic product workflows without another model turn."""
        if not isinstance(text, str) or not text.strip():
            return {"status": "failed", "message": "请输入想说的话"}
        if not self.conversation or not self.conversation.store:
            raise RuntimeError("宿主工作流需要 Conversation Store")
        if self.interaction is not None:
            await self.conversation.finish_interaction(
                self.interaction, status="interrupted", runtime="qwen_realtime")
            self._close_interaction(self.interaction)
        context = await self.conversation.begin_interaction(
            text, source="text", runtime=runtime)
        self.events.emit(
            "front.user_text", user_text=text, assistant_text="", error="")
        try:
            message = str(await handler(context) or "").strip()
            await self.conversation.finish_interaction(
                context, status="succeeded", assistant_content=message,
                runtime=runtime)
        except Exception as exc:
            message = str(exc) or "处理失败"
            await self.conversation.finish_interaction(
                context, status="failed", assistant_content=message,
                runtime=runtime)
        self.events.emit(
            "conversation.updated", user_text=text,
            assistant_text=message, error="")
        inject = getattr(self.voice, "inject_host_exchange", None)
        if inject is not None and self.is_connected:
            try:
                await inject(text, message)
            except Exception:
                # Product Session remains authoritative and restores this turn
                # after reconnect even if the live provider connection closes.
                logger.warning("host exchange not injected into live voice",
                               exc_info=True)
        return {"status": "accepted", "message": message,
                "session_id": context.session.session_id,
                "interaction_id": context.interaction_id}

    async def _connect(self, *, microphone):
        if self.is_connected and (not microphone or self.connection_microphone):
            return
        if self.voice_factory is None:
            raise RuntimeError("前台交互 Runtime 未配置")
        if microphone:
            self.events.emit("voice.connecting", voice="connecting", error="")
        conversation_history = ()
        conversation_checkpoint = ""
        stable_memory_context = ""
        session = None
        if self.conversation and self.conversation.store and self.history_builder:
            session = await self.conversation.active_session()
            if session:
                checkpoint, messages = await self.conversation.runtime_context(
                    session.session_id, "qwen_realtime", limit=None)
                restored = self.history_builder(
                    checkpoint=checkpoint, turns=messages)
                conversation_history = restored.messages
                conversation_checkpoint = restored.checkpoint
        if self.memory is not None:
            try:
                stable_memory_context = await self.memory.stable_profile()
            except Exception:
                logger.warning("stable memory profile unavailable", exc_info=True)
                stable_memory_context = ""
        self.voice = self.voice_factory(
            self._handle_tool, self.events.emit,
            microphone=microphone, playback=True,
            conversation_event=self._conversation_event,
            notification_event=self._notification_event,
            conversation_history=conversation_history,
            conversation_checkpoint=conversation_checkpoint,
            stable_memory_context=stable_memory_context,
            delegation_review=self._review_delegation,
            product_session_id=(session.session_id if session else ""))
        self.connection_microphone = microphone
        self.connection_error = None
        voice = self.voice
        self.task = asyncio.create_task(self._run(voice, microphone))
        ready = getattr(voice, "ready", None)
        if isinstance(ready, asyncio.Event):
            waiter = asyncio.create_task(ready.wait())
            done, _pending = await asyncio.wait(
                {waiter, self.task}, timeout=20,
                return_when=asyncio.FIRST_COMPLETED)
            if waiter not in done:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                if self.connection_error:
                    raise RuntimeError(str(self.connection_error))
                if self.task.done():
                    raise RuntimeError("前台交互 Runtime 已断开")
                raise TimeoutError("连接前台交互 Runtime 超时")

    async def _conversation_event(self, kind, payload):
        if not self.conversation or not self.conversation.store:
            return
        if kind == "user_final":
            if self.interaction is not None:
                await self.conversation.finish_interaction(
                    self.interaction, status="interrupted", runtime="qwen_realtime")
                self._close_interaction(self.interaction)
            transcript = str(payload.get("transcript") or "").strip()
            if transcript:
                self.interaction = await self.conversation.begin_interaction(
                    transcript, source="voice",
                    runtime="qwen_realtime")
                if self.turn_preparer is not None:
                    context = self.interaction
                    voice = self.voice
                    packet, report = await self.turn_preparer.prepare(
                        transcript, session_id=context.session.session_id,
                        query_id=context.interaction_id, context=self._decision_context(context))
                    if self.interaction is not context or self.voice is not voice or voice is None:
                        return
                    self.context_observer.turn_prepared(context.session.session_id, **report)
                    self._store_decision(context, report)
                    await voice.respond_to_query(packet, query_id=context.interaction_id)
                else:
                    await self._inject_memory_context(transcript, self.interaction)
                self._bind_pending_response(self.interaction)
                self.events.emit("conversation.updated")
        elif kind == "response_created":
            response_id = payload.get("response_id")
            origin = payload.get("origin", "user")
            if not response_id:
                return
            if origin == "tool_result":
                context = self.tool_interactions.pop(payload.get("call_id"), None)
                if context is not None:
                    self.response_interactions[response_id] = context
                return
            if origin != "user":
                return
            query_id = payload.get("query_id")
            if query_id:
                if self.interaction is not None and self.interaction.interaction_id == query_id:
                    self.response_interactions[response_id] = self.interaction
                return
            if self.interaction is not None:
                self.response_interactions[response_id] = self.interaction
            elif response_id not in self.pending_user_responses:
                # DashScope may create the response before publishing the final
                # user transcription. Keep the ordered identity until the
                # corresponding Interaction exists.
                self.pending_user_responses.append(response_id)
        elif kind == "assistant_final":
            context = self._response_interaction(payload)
            transcript = str(payload.get("transcript") or "").strip()
            if context is not None and transcript:
                await self.conversation.append_assistant_message(
                    context, transcript, runtime="qwen_realtime")
                self.events.emit("conversation.updated")
        elif kind == "response_done":
            context = self._response_interaction(payload)
            self._discard_pending_response(payload.get("response_id"))
            if context is None:
                return
            if payload.get("cancelled"):
                await self.conversation.finish_interaction(
                    context, status="cancelled", runtime="qwen_realtime")
                self._close_interaction(context)
                self.events.emit("conversation.updated")
            elif not payload.get("has_tool_calls") and not payload.get("delegation_review_pending"):
                await self.conversation.finish_interaction(
                    context, status="succeeded", runtime="qwen_realtime")
                self._close_interaction(context)
                self.events.emit("conversation.updated")

    def _response_interaction(self, payload):
        response_id = payload.get("response_id")
        context = (self.response_interactions.get(response_id)
                   if response_id else self.interaction)
        if context is None or context.interaction_id in self.closed_interactions:
            return None
        return context

    async def _inject_memory_context(self, query, context):
        if self.memory is None or self.voice is None or context is None:
            return
        inject = getattr(self.voice, "inject_memory_context", None)
        if inject is None:
            return
        session_id = context.session.session_id
        started = time.perf_counter()
        packet, status = "", "empty"
        try:
            packet = await self.memory.context_packet(
                query, session_id=session_id, top_k=5)
            if packet:
                await inject(packet)
                status = "injected"
        except Exception as exc:
            # A recall timeout must never block or fail the foreground turn.
            status = "failed:" + type(exc).__name__
            logger.warning("memory injection failed: %s", status, exc_info=True)
        finally:
            self.context_observer.turn_prepared(
                session_id,
                memory={"status": status,
                        "ms": round((time.perf_counter() - started) * 1000, 1),
                        "chars": len(packet or "")})

    def _bind_pending_response(self, context):
        while self.pending_user_responses:
            response_id = self.pending_user_responses.pop(0)
            if response_id not in self.response_interactions:
                self.response_interactions[response_id] = context
                return

    def _discard_pending_response(self, response_id):
        if response_id:
            self.pending_user_responses = [
                item for item in self.pending_user_responses
                if item != response_id]

    def _clear_response_tracking(self):
        self.pending_user_responses.clear()
        self.response_interactions.clear()
        self.tool_interactions.clear()
        self.decisions.clear()

    def _close_interaction(self, context):
        self.closed_interactions.add(context.interaction_id)
        self.decisions.pop(context.interaction_id, None)
        if self.interaction is context:
            self.interaction = None
        for response_id, item in tuple(self.response_interactions.items()):
            if item is context:
                self.response_interactions.pop(response_id, None)

    async def _handle_tool(self, name, arguments, metadata=None):
        metadata = metadata or {}
        interaction = self._response_interaction(metadata)
        if name == "run_task" and interaction is None:
            return {"status": "failed", "message": "此请求已被打断或结束，未启动任务"}
        advice = self.decisions.get(interaction.interaction_id) if interaction is not None else None
        if name == "run_task" and advice is not None and advice.exclusion is not None:
            # The lightweight first-pass decision is advice, not an absolute
            # veto. If Qwen explicitly called run_task, resolve the conflict
            # with the stricter query+reply semantic reviewer. This repairs
            # false negatives such as “关闭计算器” while punctuation, chat and
            # status questions remain rejected when review is absent or fails.
            approved = await self._review_explicit_delegation(
                interaction, str(metadata.get("reply") or ""))
            if approved:
                result = await self.handle_tool(
                    name, arguments, interaction=interaction)
            else:
                result = {
                    "status": "not_delegated", "reason": advice.exclusion,
                    "message": ("本轮决策认为是已有任务的进度询问。请调用 task_status 后自然回答。"
                                if advice.exclusion == "status" else
                                "本轮决策认为是聊天或澄清，未创建新任务。请自然回应或询问用户。"),
                }
        else:
            result = await self.handle_tool(name, arguments, interaction=interaction)
        if name == "run_task" and interaction is not None and result.get("status") in {"accepted", "queued"}:
            self._close_interaction(interaction)
        elif interaction is not None and (metadata or {}).get("call_id"):
            self.tool_interactions[(metadata or {})["call_id"]] = interaction
        return result

    async def _review_explicit_delegation(self, interaction: InteractionContext,
                                          reply: str) -> bool:
        if self.turn_preparer is None or not reply.strip():
            return False
        approved = await self.turn_preparer.review_delegation(
            interaction.user_event.content or "", reply,
            context=self._decision_context(interaction))
        return bool(approved and self.interaction is interaction
                    and interaction.interaction_id not in self.closed_interactions)

    @staticmethod
    def _decision_context(context: InteractionContext) -> str:
        # Stable chronological suffix; excludes ephemeral references and old
        # decision scores. Short confirmations can refer to the last exchange.
        return "\n".join(f"{event.role}: {event.content}" for event in context.prior_messages[-8:]
                         if event.content)[-2400:]

    def _store_decision(self, context: InteractionContext, report: dict[str, object]) -> None:
        advice = DecisionAdvice.from_payload(report.get("jev"))
        if advice is not None:
            self.decisions[context.interaction_id] = advice

    async def _review_delegation(self, response_id: str, reply: str) -> bool:
        context = self._response_interaction({"response_id": response_id})
        if context is None or self.turn_preparer is None:
            return False
        advice = self.decisions.get(context.interaction_id)
        if advice is None or advice.exclusion is not None:
            return False
        approved = await self.turn_preparer.review_delegation(
            context.user_event.content or "", reply, context=self._decision_context(context))
        # A new utterance may have interrupted while the model was reviewing.
        return bool(approved and self._response_interaction({"response_id": response_id}) is context)

    async def deliver_notification(self, item):
        if not self.is_connected:
            return False
        notify = getattr(self.voice, "notify_task_result", None)
        if notify is None:
            return False
        await notify(item)
        return True

    async def _notification_event(self, kind, payload):
        if self.notification_event is None:
            return
        result = self.notification_event(kind, payload)
        if asyncio.iscoroutine(result):
            await result

    async def _run(self, voice, microphone):
        reconnect = False
        try:
            await voice.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.connection_error = exc
            message = str(exc) or "语音连接失败"
            reconnect = (
                microphone and self.voice_requested
                and _is_reconnectable_voice_disconnect(message)
            )
            if reconnect:
                self.events.emit("voice.reconnecting", voice="connecting",
                                 speaking=False, user_speaking=False, error="")
            elif _is_idle_realtime_disconnect(message):
                # DashScope closes idle text-only Realtime sessions after 180s.
                # Text reconnects on demand. Voice returns to the off state and
                # can be reopened without exposing the provider close frame.
                self.events.emit("front.idle_disconnected", error="")
            else:
                self.events.emit("voice.error", error=message)
        finally:
            self._clear_response_tracking()
            if self.voice is voice:
                self.voice = None
                self.connection_microphone = False
            if reconnect:
                self._schedule_voice_reconnect()
            elif microphone:
                self.events.emit("voice.off", voice="off", speaking=False,
                                 user_speaking=False)

    def _schedule_voice_reconnect(self):
        if self.reconnect_task and not self.reconnect_task.done():
            return

        async def reconnect():
            last_error = None
            for delay in self.reconnect_delays:
                if delay:
                    await asyncio.sleep(delay)
                if not self.voice_requested:
                    return
                try:
                    await self._connect(microphone=True)
                    if self.is_connected:
                        return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                    self.events.emit(
                        "voice.reconnecting", voice="connecting", error="")
            if self.voice_requested:
                self.voice_requested = False
                detail = str(last_error or "连接失败")
                self.events.emit(
                    "voice.error", error=f"语音自动重连失败：{detail}")
                self.events.emit(
                    "voice.off", voice="off", speaking=False,
                    user_speaking=False)

        task = asyncio.create_task(reconnect())
        self.reconnect_task = task

        def clear(done):
            if self.reconnect_task is done:
                self.reconnect_task = None

        task.add_done_callback(clear)

    async def stop(self):
        self.voice_requested = False
        reconnect_task, self.reconnect_task = self.reconnect_task, None
        if reconnect_task and reconnect_task is not asyncio.current_task():
            reconnect_task.cancel()
            await asyncio.gather(reconnect_task, return_exceptions=True)
        voice, task = self.voice, self.task
        self.voice = None
        self.connection_microphone = False
        if voice:
            with contextlib.suppress(Exception):
                await voice.stop()
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), 5)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def close(self):
        await self.stop()

    async def reset_session(self):
        """Detach provider state before the active Product Session changes."""
        if (self.interaction is not None and self.conversation
                and self.conversation.store):
            await self.conversation.finish_interaction(
                self.interaction, status="interrupted",
                runtime="qwen_realtime")
            self._close_interaction(self.interaction)
        await self.stop()
        self._clear_response_tracking()
