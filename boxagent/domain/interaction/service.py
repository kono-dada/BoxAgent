"""Voice lifecycle independent from the concrete realtime provider."""

import asyncio
import contextlib


class InteractionService:
    def __init__(self, events, voice_factory, handle_tool, *, conversation=None,
                 history_builder=None, notification_event=None,
                 memory_context_provider=None):
        self.events = events
        self.voice_factory = voice_factory
        self.handle_tool = handle_tool
        self.conversation = conversation
        self.history_builder = history_builder
        self.notification_event = notification_event
        self.memory_context_provider = memory_context_provider
        self.voice = self.task = None
        self.connection_microphone = False
        self.connection_error = None
        self.interaction = None
        self.response_interactions = {}
        self.tool_interactions = {}
        self.pending_user_responses = []
        self.closed_interactions = set()
        self.toggle_lock = asyncio.Lock()

    @property
    def is_connected(self):
        return bool(self.voice and self.task and not self.task.done())

    async def toggle(self):
        async with self.toggle_lock:
            if self.is_connected and self.connection_microphone:
                self.events.emit("voice.stopping", voice="stopping",
                                 speaking=False, user_speaking=False)
                await self.stop()
                return
            if self.is_connected:
                await self.stop()
            await self._connect(microphone=True)

    async def submit_text(self, text):
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
        self.events.emit("front.user_text", user_text=text, assistant_text="", error="")
        try:
            submit = getattr(self.voice, "submit_text", None)
            if submit is None:
                raise RuntimeError("当前前台 Runtime 不支持文字输入")
            await submit(text)
        except Exception:
            if self.interaction is not None:
                await self.conversation.finish_interaction(
                    self.interaction, status="failed", runtime="qwen_realtime")
                self._close_interaction(self.interaction)
            raise
        result = {"status": "accepted"}
        if self.interaction is not None:
            result.update(session_id=self.interaction.session.session_id,
                          interaction_id=self.interaction.interaction_id)
        return result

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
        if self.conversation and self.conversation.store and self.history_builder:
            session = await self.conversation.active_session()
            if session:
                checkpoint, messages = await self.conversation.runtime_context(
                    session.session_id, "qwen_realtime", limit=None)
                restored = self.history_builder(
                    checkpoint=checkpoint, turns=messages)
                conversation_history = restored.messages
                conversation_checkpoint = restored.checkpoint
        if self.memory_context_provider is not None:
            try:
                stable_memory_context = await self.memory_context_provider.stable_profile()
            except Exception:
                stable_memory_context = ""
        self.voice = self.voice_factory(
            self._handle_tool, self.events.emit,
            microphone=microphone, playback=True,
            conversation_event=self._conversation_event,
            notification_event=self._notification_event,
            conversation_history=conversation_history,
            conversation_checkpoint=conversation_checkpoint,
            stable_memory_context=stable_memory_context)
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
                self._bind_pending_response(self.interaction)
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
        elif kind == "response_done":
            context = self._response_interaction(payload)
            self._discard_pending_response(payload.get("response_id"))
            if context is None:
                return
            if payload.get("cancelled"):
                await self.conversation.finish_interaction(
                    context, status="cancelled", runtime="qwen_realtime")
                self._close_interaction(context)
            elif not payload.get("has_tool_calls"):
                await self.conversation.finish_interaction(
                    context, status="succeeded", runtime="qwen_realtime")
                self._close_interaction(context)

    def _response_interaction(self, payload):
        response_id = payload.get("response_id")
        context = (self.response_interactions.get(response_id)
                   if response_id else self.interaction)
        if context is None or context.interaction_id in self.closed_interactions:
            return None
        return context

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

    def _close_interaction(self, context):
        self.closed_interactions.add(context.interaction_id)
        if self.interaction is context:
            self.interaction = None
        for response_id, item in tuple(self.response_interactions.items()):
            if item is context:
                self.response_interactions.pop(response_id, None)

    async def _handle_tool(self, name, arguments, metadata=None):
        interaction = self._response_interaction(metadata or {})
        try:
            return await self.handle_tool(name, arguments, interaction=interaction)
        finally:
            if name == "run_task" and interaction is not None:
                self._close_interaction(interaction)
            elif interaction is not None and (metadata or {}).get("call_id"):
                self.tool_interactions[metadata["call_id"]] = interaction

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
        try:
            await voice.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.connection_error = exc
            self.events.emit("voice.error", error=str(exc) or "语音连接失败")
        finally:
            self._clear_response_tracking()
            if self.voice is voice:
                self.voice = None
                self.connection_microphone = False
            if microphone:
                self.events.emit("voice.off", voice="off", speaking=False,
                                 user_speaking=False)

    async def stop(self):
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
