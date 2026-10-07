"""千问实时语音适配器；工具回传排队，接收循环始终可继续收听。"""

import asyncio
import base64
import contextlib
import json
import re
import time
from collections import deque

import websockets

from boxagent.infrastructure.audio import AudioIO

MODELS = {
    "qwen-audio-3.0-realtime-plus": {"voice": "longanqian", "turn_detection": {"type": "smart_turn"}},
    "qwen3.5-omni-flash-realtime": {"voice": "Ethan", "turn_detection": {
        "type": "semantic_vad", "threshold": 0.5, "silence_duration_ms": 800}},
    "qwen3.8-omni-flash-realtime": {
        "voice": "Tina",
        "input_audio_format": "pcm",
        "output_audio_format": "pcm",
        "turn_detection": {
            "type": "semantic_vad", "threshold": 0.5,
            # Natural spoken requests often contain a short thinking pause.
            # Give the user enough time to continue before committing the turn.
            "silence_duration_ms": 1500,
        },
    },
}


def is_qwen_idle_timeout(value):
    """Recognize provider variants of the normal 180-second idle close."""
    if isinstance(value, dict):
        error = value.get("error") or value
        code = str(error.get("code") or "").lower()
        message = str(error.get("message") or "").lower()
    else:
        code = ""
        message = str(value or "").lower()
    return code == "response_idle_timeout" or (
        "180 seconds" in message
        and any(marker in message for marker in (
            "no user input was received",
            "no response was generated",
        )))


def tool(name, description, properties=None, required=None):
    properties = properties or {}
    required = list(properties) if required is None else list(required)
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = [
    tool("run_task", "将当前这条用户原始请求委托给后台通用桌面操作 Agent。"
         "宿主会从当前 Interaction 读取原文，不要重述、改写或补充目标参数。"
         "后台会自行观察界面、规划并执行，可能需要几十秒到几分钟；"
         "调用前先简短语音回应，返回前不能假称完成。"),
    tool("cancel_task", "用户明确要求取消或停止后台任务时调用；一般插话、问问题不等于取消任务。"),
    tool("task_status", "查询后台任务进度或最近的真实结果。"),
    tool("remember_memory", "仅在用户明确要求‘记住’某个长期有用的偏好、约定或事实时调用；不要把普通闲聊自动存入长期记忆。",
         {"content": {"type": "string", "description": "用完整、自包含的文本记录用户希望长期保留的信息。"}}),
    tool("recall_memory", "当用户询问过去的偏好、约定、事件或要求删除某条记忆时调用。返回的记忆 ID 可用于精确删除。",
         {"query": {"type": "string", "description": "要回忆的人、偏好、事件或关键词。"}}),
    tool("forget_memory", "删除用户明确要求忘记的长期记忆。只能使用 recall_memory 返回的精确 ID；如果匹配不唯一，先向用户确认，不得猜测或批量模糊删除。",
         {"memory_ids": {"type": "array", "items": {"type": "string"},
                         "description": "recall_memory 返回且用户确认删除的记忆 ID。"}}),
    tool("find_skill", "用户询问已有 Skill、准备创建 Skill 或希望从既有能力中寻找方案时调用。只搜索 BoxAgent 当前已安装的安全 Skill。",
         {"query": {"type": "string", "description": "简短的能力关键词，可使用中英文。"},
          "limit": {"type": "integer", "minimum": 1, "maximum": 20}},
         required=["query"]),
    tool("prepare_skill", "用户要求创建、沉淀或优化 Skill 时调用。宿主会读取当前用户原话、最近完成任务的真实轨迹和现有 Skill，使用独立只读 Codex Turn 生成草稿；不要自行编造 Skill 内容。"),
    tool("install_skill_draft", "安装 prepare_skill 返回的草稿。只有当前用户消息已经明确要求创建、安装、更新，或明确确认上一轮草稿时才调用。",
         {"draft_id": {"type": "string", "description": "可选；宿主会优先解析当前 Session 最新待确认草稿。"}},
         required=[]),
]

INSTRUCTIONS = (
    "你是常驻在用户 Mac 桌面的个人伙伴，用简短自然的中文和用户对话。"
    "你的姓名、自我介绍、性格和语气必须以后续用户角色设定为准，不要自称产品名、系统名或模型名。"
    "用户要求操作电脑或应用时调用 run_task；不要重述或改写目标，后台执行器会读取当前用户消息原文。"
    "把操作请求直接交给后台，根据工具实际反馈判断可执行性。"
    "后台自己发现应用、读界面、执行和判断结果；前台不要替它编造固定点击流程。"
    "关键交互：调用前先简短说你开始处理、用户可以继续聊天，并且必须在同一个 response 中实际调用 run_task。"
    "只说‘我开始处理’却不调用 run_task 属于协议错误；通常需要几十秒到几分钟。"
    "不要在工具返回前假称目标已完成；blocked 或 failed 要如实解释。等待工具时仍可回应用户的其他话题。"
    "用户说取消任务时调用 cancel_task；用户仅仅插话不取消任务。工具失败或取消时如实报告，不能冒充成功。"
    "只有用户明确说要记住时才调用 remember_memory，不要自动保存普通对话。"
    "回答用户过去的偏好、约定或事件前，先调用 recall_memory，并只根据返回内容回答。"
    "用户要求忘记时，先用 recall_memory 找到精确 ID；匹配不唯一时先确认，再调用 forget_memory。"
    "用户询问已有 Skill 时调用 find_skill。用户要求创建、沉淀或优化 Skill 时，不要调用 run_task；"
    "先调用 find_skill 避免重复，再调用 prepare_skill。prepare_skill 只会启动后台生成，"
    "返回 accepted 后立即告诉用户正在生成，不要等待或重复调用。"
    "若当前用户原话已经明确要求创建、安装、更新或优化，可继续调用 install_skill_draft；"
    "否则展示草稿摘要并等待用户明确确认，确认前不得写入。"
    "只有 install_skill_draft 返回 installed 后才能声称 Skill 已安装。"
    "收到以[可信任务结果]开头的消息时，它是后台任务的最终事实；"
    "用一句简短口语通知用户，不添加未提供的结果，也不要再次调用run_task。"
    "每次一般回答一到两句话，不要大段自我介绍。"
)


class QwenRealtimeSession:
    def __init__(self, handle_tool, emit, *, model="qwen3.8-omni-flash-realtime", key=None,
                 microphone=True, playback=True, trace=None, audio_factory=AudioIO,
                 conversation_event=None, notification_event=None,
                 conversation_history=(), instructions=INSTRUCTIONS,
                 environment_context=None, conversation_checkpoint="",
                 stable_memory_context="", product_session_id=""):
        self.handle_tool, self.emit = handle_tool, emit
        self.model, self.microphone, self.playback = model, microphone, playback
        self.key = key
        self.audio_factory = audio_factory
        raw_trace = trace or (lambda *_args, **_kwargs: None)
        self.product_session_id = str(product_session_id or "")
        self.trace = lambda event, **data: raw_trace(
            event, product_session_id=self.product_session_id, **data)
        self.conversation_event = conversation_event
        self.notification_event = notification_event
        self.conversation_history = tuple(conversation_history)
        self.conversation_checkpoint = str(conversation_checkpoint or "")
        self.stable_memory_context = str(stable_memory_context or "")
        self.instructions = instructions
        self.environment_context = environment_context
        self.ws = self.audio = None
        self.ready = asyncio.Event()
        self.seen_calls = set()
        self.response_calls = {}
        self.finished_responses = set()
        self.response_finalizers = {}
        self.deliveries = set()
        self.results = asyncio.Queue()
        self.notifications = asyncio.Queue()
        self.delivery_lock = asyncio.Lock()
        self.response_id = None
        self.response_active = self.user_speaking = self.playing = False
        self.cancelled_responses = set()
        self.final_transcripts = set()
        self.response_transcripts = {}
        # response.create and response.created are asynchronous. A second text
        # turn may be submitted before the server acknowledges the tool-result
        # response from the previous turn, so one mutable "next origin" slot is
        # insufficient. Preserve request order until the provider assigns IDs.
        self.response_origins = deque()
        self.response_context = {}
        self.notification_responses = {}
        self.pending_notifications = set()
        self.playing_notification_id = None
        self.text = ""
        self.closed = False
        self.last_user_activity = 0.0
        self.input_turn_pending = False
        self.input_turn_settled = asyncio.Event()
        self.input_turn_settled.set()

    async def send(self, message):
        if self.closed or self.ws is None:
            raise ConnectionError("语音会话已关闭")
        if message.get("type") != "input_audio_buffer.append":
            self.trace("client.request", request=message)
        await self.ws.send(json.dumps(message))

    def playback_changed(self, active):
        self.playing = active
        if not self.closed:
            self.emit("voice.playback", speaking=active)
            self.trace("playback", active=active)
        if active and self.playing_notification_id:
            notification_id, self.playing_notification_id = (
                self.playing_notification_id, None)
            self.pending_notifications.discard(notification_id)
            self._dispatch_notification_event(
                "playback_started", notification_id=notification_id)

    async def run(self):
        config = MODELS.get(self.model)
        if not config:
            raise ValueError("不支持的语音模型配置")
        if not self.key:
            raise RuntimeError("未配置千问密钥，请在 .env.local 中配置 DASHSCOPE_API_KEY")
        loop = asyncio.get_running_loop()
        workers = []
        try:
            opening = asyncio.create_task(asyncio.to_thread(self.audio_factory,
                lambda active: loop.call_soon_threadsafe(self.playback_changed, active),
                microphone=self.microphone, playback=self.playback))
            try:
                self.audio = await asyncio.shield(opening)
            except asyncio.CancelledError:
                self.audio = await opening
                raise
            try:
                async with websockets.connect(
                    f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={self.model}",
                    additional_headers={"Authorization": f"Bearer {self.key}"}, max_size=16 * 1024 * 1024,
                    open_timeout=15, close_timeout=1, ping_interval=20, ping_timeout=20,
                ) as self.ws:
                    await self.configure_session(config)
                    workers.append(asyncio.create_task(self.guarded_delivery()))
                    workers.append(asyncio.create_task(self.guarded_notifications()))
                    if self.microphone:
                        workers.append(asyncio.create_task(self.send_mic()))
                    async for raw in self.ws:
                        await self.receive(json.loads(raw))
            except websockets.ConnectionClosed as exc:
                if not is_qwen_idle_timeout(exc):
                    raise
                self.trace("transport.idle_disconnected", transport="websocket",
                           reason=str(exc))
        finally:
            self.closed = True
            for notification_id in tuple(self.pending_notifications):
                self._dispatch_notification_event(
                    "delivery_interrupted", notification_id=notification_id,
                    error="realtime_connection_closed")
            self.pending_notifications.clear()
            cleanup = asyncio.create_task(self.close_resources(workers))
            # 外层会话取消不能跳过设备关闭，否则原生音频回调可能活到 Python 退出之后。
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()

    async def configure_session(self, config):
        """Install stable policy first, then restore native Product Session messages."""
        await self.send_session_update(config)
        await self.inject_history()

    async def send_session_update(self, config):
        """Send the provider session handshake without restoring history yet."""
        await self.send({"type": "session.update", "session": {
            "modalities": ["text", "audio"], **config,
            "instructions": self.instructions,
            "tools": TOOLS}})

    async def inject_history(self):
        """Restore bounded Product Session history as native Realtime messages."""
        if self.stable_memory_context:
            await self.send({"type": "conversation.item.create", "item": {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text",
                             "text": self.stable_memory_context}],
            }})
            self.trace("stable_profile.injected",
                       character_count=len(self.stable_memory_context))
        if self.conversation_checkpoint:
            await self.send({"type": "conversation.item.create", "item": {
                "type": "message",
                "role": "system",
                "content": [{"type": "input_text",
                             "text": self.conversation_checkpoint}],
            }})
            self.trace("checkpoint.injected",
                       character_count=len(self.conversation_checkpoint))
        for message in self.conversation_history:
            if message.role not in {"user", "assistant"}:
                raise ValueError("千问历史消息只支持 user/assistant role")
            content_type = "input_text" if message.role == "user" else "output_text"
            await self.send({"type": "conversation.item.create", "item": {
                "type": "message",
                "role": message.role,
                "content": [{"type": content_type, "text": message.content}],
            }})
        if self.conversation_history:
            self.trace(
                "history.injected",
                message_count=len(self.conversation_history),
                first_sequence=self.conversation_history[0].sequence,
                last_sequence=self.conversation_history[-1].sequence)

    async def close_resources(self, workers):
        children = [*workers, *self.deliveries]
        for task in children:
            task.cancel()
        await asyncio.gather(*children, return_exceptions=True)
        if self.audio:
            await asyncio.to_thread(self.audio.close)

    async def send_mic(self):
        await self.ready.wait()
        try:
            while not self.closed:
                chunk = await asyncio.to_thread(self.audio.read)
                await self.feed_audio(chunk)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.closed:
                self.emit("voice.error", error="麦克风中断：" + str(exc))
                await self.stop()

    async def feed_audio(self, chunk):
        await self.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode("ascii")})

    async def receive(self, event):
        kind = event.get("type")
        if kind not in {"response.audio.delta", "response.audio_transcript.delta",
                        "response.text.delta",
                        "conversation.item.input_audio_transcription.delta"}:
            self.trace(kind, **{key: value for key, value in event.items() if key in {"call_id", "name", "arguments", "transcript", "error"}})
        if kind == "session.updated":
            self.ready.set()
            if self.microphone:
                self.emit("voice.ready", voice="ready", error="")
            else:
                self.emit("front.ready", error="")
        elif kind == "response.created":
            self.response_active = True
            self.response_id = event.get("response", {}).get("id")
            self.text = ""
            origin = self.response_origins.popleft() if self.response_origins else None
            if self.response_id:
                self.response_context[self.response_id] = dict(origin or {"kind": "user"})
            if (origin or {}).get("kind") == "notification" and self.response_id:
                self.notification_responses[self.response_id] = origin["notification_id"]
            await self._record("response_created", response_id=self.response_id,
                               origin=(origin or {}).get("kind", "user"),
                               call_id=(origin or {}).get("call_id"),
                               notification_id=(origin or {}).get("notification_id"))
        elif kind == "input_audio_buffer.speech_started":
            self.user_speaking = True
            self.input_turn_pending = True
            self.input_turn_settled.clear()
            self.last_user_activity = time.monotonic()
            # An AOQ session emits silent playback frames while its native
            # device is warming up. Do not interrupt the player merely because
            # the first user turn started; only clear real in-flight output.
            if self.response_active or self.playing:
                self.audio.clear()
            self.emit("voice.listening", user_speaking=True, speaking=False,
                      user_text="")
            if self.response_active and self.response_id:
                self.cancelled_responses.add(self.response_id)
                await self.send({"type": "response.cancel"})
            await self.inject_environment()
        elif kind == "input_audio_buffer.speech_stopped":
            self.user_speaking = False
            self.last_user_activity = time.monotonic()
            self.emit("voice.input_stopped", user_speaking=False)
        elif kind == "conversation.item.input_audio_transcription.delta":
            # DashScope splits live ASR into a stable prefix (`text`) and an
            # unstable suffix (`stash`). This is presentation-only: the
            # Product Session and memory pipeline still receive only the final
            # `completed` transcript below.
            preview = str(event.get("text") or "") + str(event.get("stash") or "")
            self.emit("voice.user_text_delta", user_text=preview)
        elif kind == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript", "")
            self.emit("voice.user_text", user_text=transcript)
            await self._record("user_final", transcript=transcript,
                               item_id=event.get("item_id"))
        elif kind == "response.audio.delta":
            if not self.user_speaking and event.get("response_id") not in self.cancelled_responses:
                if notification_id := self.notification_responses.get(event.get("response_id")):
                    self.playing_notification_id = notification_id
                self.audio.append(event["delta"])
        elif kind in {"response.audio_transcript.delta", "response.text.delta"}:
            if event.get("response_id") not in self.cancelled_responses:
                self.text += event.get("delta", "")
                if event.get("response_id") not in self.notification_responses:
                    self.emit("voice.assistant_text", assistant_text=self.text)
        elif kind in {"response.audio_transcript.done", "response.text.done"}:
            response_id = event.get("response_id")
            if response_id not in self.cancelled_responses:
                transcript = event.get("transcript", event.get("text", ""))
                if response_id not in self.notification_responses:
                    self.emit("voice.assistant_text", assistant_text=transcript)
                if transcript:
                    self.response_transcripts[response_id] = transcript
        elif kind == "response.audio.done":
            self._schedule_response_finalizer(event.get("response_id"))
        elif kind == "response.function_call_arguments.done":
            # The arguments-done event is the provider's executable boundary.
            # AOQ may deliver response.done much later than the audio/data
            # output, so waiting for it makes desktop tasks appear stuck.
            self._dispatch_tool_call({
                **event,
                "response_id": event.get("response_id") or self.response_id,
            })
        elif kind == "response.done":
            response = event.get("response", {})
            response_id = response.get("id") or self.response_id
            # Some provider versions omit the incremental arguments event.
            # Recover and dispatch those calls here, still without blocking
            # the receive loop.
            recovered_calls = self._calls_from_response_output(response, response_id)
            for call in recovered_calls:
                self._dispatch_tool_call(call)
            await self._finalize_response(response_id, source="provider")
        elif kind == "error":
            error = event.get("error", {})
            message = error.get("message", "语音服务发生错误")
            idle_timeout = is_qwen_idle_timeout(event)
            # VAD 可能已经抢先取消，本地重复取消无须中断会话。
            if (not idle_timeout and "no active response" not in message.lower()
                    and "no response" not in message.lower()):
                self.emit("voice.error", error=message)
            if not self.ready.is_set():
                raise RuntimeError(message)

    async def _record(self, kind, **payload):
        if self.conversation_event is None:
            return
        result = self.conversation_event(kind, payload)
        if asyncio.iscoroutine(result):
            await result

    async def run_tool(self, call):
        try:
            result = await self.handle_tool(
                call.get("name"), call.get("arguments", "{}"),
                {"response_id": call.get("response_id"),
                 "call_id": call.get("call_id")})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {"status": "failed", "message": str(exc)}
        self.trace("tool.result", call_id=call["call_id"], result=result)
        await self.results.put((call["call_id"], call["name"], result))

    def _dispatch_tool_call(self, call):
        """Start one provider tool call exactly once without awaiting it."""
        call_id = call.get("call_id") or call.get("id")
        name = call.get("name") or (call.get("function") or {}).get("name")
        arguments = call.get("arguments")
        if arguments is None:
            arguments = (call.get("function") or {}).get("arguments", "{}")
        response_id = call.get("response_id") or self.response_id
        if not call_id or not name or call_id in self.seen_calls:
            return False
        normalized = {
            "call_id": call_id,
            "name": name,
            "arguments": arguments or "{}",
            "response_id": response_id,
        }
        self.seen_calls.add(call_id)
        if response_id:
            self.response_calls.setdefault(response_id, set()).add(call_id)
        task = asyncio.create_task(self.run_tool(normalized))
        self.deliveries.add(task)
        task.add_done_callback(self.deliveries.discard)
        self.trace(
            "tool.dispatched", call_id=call_id, name=name,
            response_id=response_id)
        return True

    def _schedule_response_finalizer(self, response_id, delay=.35):
        """Close AOQ responses that omit or substantially delay response.done."""
        response_id = response_id or self.response_id
        if not response_id or response_id in self.finished_responses:
            return
        previous = self.response_finalizers.pop(response_id, None)
        if previous:
            previous.cancel()

        async def finalize():
            try:
                await asyncio.sleep(delay)
                await self._finalize_response(response_id, source="audio_done")
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(finalize())
        self.response_finalizers[response_id] = task
        task.add_done_callback(
            lambda done, current=response_id:
            self.response_finalizers.pop(current, None)
            if self.response_finalizers.get(current) is done else None)

    async def _finalize_response(self, response_id, *, source):
        if not response_id or response_id in self.finished_responses:
            return
        self.finished_responses.add(response_id)
        timer = self.response_finalizers.pop(response_id, None)
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        if self.response_id == response_id:
            self.response_active = False
        has_tool_calls = bool(self.response_calls.pop(response_id, set()))
        cancelled = response_id in self.cancelled_responses
        notification_id = self.notification_responses.pop(response_id, None)
        origin = self.response_context.pop(response_id, {"kind": "user"})
        if origin.get("kind") == "user" and self.input_turn_pending:
            self.input_turn_pending = False
            self.input_turn_settled.set()
        if notification_id and cancelled:
            self.pending_notifications.discard(notification_id)
            self._dispatch_notification_event(
                "delivery_interrupted", notification_id=notification_id,
                error="notification_response_cancelled")
        transcript = self.response_transcripts.pop(response_id, "") or self.text
        if not cancelled and response_id not in self.final_transcripts and transcript.strip():
            self.final_transcripts.add(response_id)
            await self._record("assistant_final", transcript=transcript,
                               response_id=response_id)
        implicit_delegation = (
            not cancelled
            and origin.get("kind") == "user"
            and not has_tool_calls
            and self._implies_background_delegation(transcript)
        )
        await self._record(
            "response_done", response_id=response_id,
            cancelled=cancelled,
            has_tool_calls=has_tool_calls or implicit_delegation,
            implicit_delegation=implicit_delegation)
        if source != "provider":
            self.trace("response.synthetic_done", response_id=response_id,
                       source=source)
        if implicit_delegation:
            task = asyncio.create_task(
                self.run_implicit_delegation(response_id, transcript))
            self.deliveries.add(task)
            task.add_done_callback(self.deliveries.discard)

    async def run_implicit_delegation(self, response_id, transcript):
        """Repair a provider turn that promised execution but omitted run_task.

        Qwen has already made the routing decision in natural language. The
        host therefore delegates the authoritative Product Interaction without
        synthesizing or rewriting a goal. There is no provider function call to
        answer, so the acceptance result is traced rather than injected as an
        unmatched function_call_output item.
        """
        try:
            result = await self.handle_tool(
                "run_task", "{}",
                {"response_id": response_id,
                 "call_id": f"implicit-run-task:{response_id}"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {"status": "failed", "message": str(exc)}
        self.trace(
            "tool.implicit_run_task", response_id=response_id,
            transcript=transcript, result=result)
        if result.get("status") not in {"accepted", "queued"}:
            self.emit(
                "voice.error",
                error=result.get("message", "后台任务未能启动，请重试"))

    def _calls_from_response_output(self, response, response_id):
        """Recover calls when a provider omits the incremental arguments event."""
        recovered = []
        for item in response.get("output") or ():
            if item.get("type") not in {"function_call", "tool_call"}:
                continue
            call_id = item.get("call_id") or item.get("id")
            name = item.get("name") or (item.get("function") or {}).get("name")
            arguments = item.get("arguments")
            if arguments is None:
                arguments = (item.get("function") or {}).get("arguments", "{}")
            if not call_id or not name or call_id in self.seen_calls:
                continue
            recovered.append({
                "call_id": call_id,
                "name": name,
                "arguments": arguments or "{}",
                "response_id": response_id,
            })
        return recovered

    @staticmethod
    def _implies_background_delegation(transcript):
        """Conservatively recognize an affirmative execution commitment."""
        text = "".join(str(transcript or "").split())
        if not text:
            return False
        # Invitations and capability statements are conversation, not an
        # execution promise. In particular, a self-introduction commonly ends
        # with “想让我帮你处理点什么？”.
        if any(marker in text for marker in (
                "想让我帮你", "要不要我帮你", "需要我帮你", "可以让我帮你",
                "如果你愿意", "你可以让我", "有什么需要我")):
            return False
        if any(marker in text for marker in (
                "不能处理", "无法处理", "不会处理", "还没开始", "尚未开始",
                "没有开始", "不要处理", "别处理", "不能操作", "无法操作")):
            return False
        if any(marker in text for marker in (
            "我开始处理", "已经开始处理", "正在处理", "我来处理", "我去处理",
            "我帮你处理", "我继续处理", "这就处理", "马上处理", "我开始执行", "我去执行",
            "这就执行", "我来操作", "我去操作", "这就操作", "交给我",
            "我再试一次", "我再试试", "我重新试一下", "我重新试试",
            "我再帮你操作一次", "这就重试",
        )):
            return True
        # Catch concrete desktop commitments without making broad phrases such
        # as “我这就把原因告诉你” look like computer operations.
        action = (r"打开|关闭|最小化|播放|暂停|停(?:止|掉|下|了)|收藏|发送|点击|输入|"
                  r"切换|调整|保存|下载|安装|启动|退出|搜索|查找")
        commitment = re.search(
            rf"(?:我)?(?:现在|马上|立刻|这就|继续)?(?:来|去)?帮你"
            rf"[^。！？?!]{{0,24}}(?:{action})", text)
        if not commitment:
            commitment = re.search(
                rf"我(?:现在|马上|立刻|这就|继续)(?:来|去)?把"
                rf"[^。！？?!]{{0,24}}(?:{action})", text)
        return bool(commitment)

    async def submit_text(self, text):
        await self.ready.wait()
        async with self.delivery_lock:
            if self.response_active and self.response_id:
                self.cancelled_responses.add(self.response_id)
                await self.send({"type": "response.cancel"})
                if self.audio:
                    self.audio.clear()
            await self.inject_environment()
            await self.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }})
            await self._create_response({"kind": "user"})

    async def inject_environment(self):
        """Append a fresh host snapshot immediately before the next user item."""
        source = self.environment_context
        try:
            packet = source() if callable(source) else source
        except Exception as exc:
            self.trace("environment.capture_failed", error=str(exc))
            return
        if not isinstance(packet, str) or not packet.strip():
            return
        await self.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "system",
            "content": [{"type": "input_text", "text": packet.strip()}],
        }})

    async def inject_memory_context(self, packet):
        """Append per-turn L2 memory before the current response is created."""
        if not isinstance(packet, str) or not packet.strip():
            return
        await self.ready.wait()
        await self.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "system",
            "content": [{"type": "input_text", "text": packet.strip()}],
        }})
        self.trace("memory_context.injected", character_count=len(packet))

    async def inject_host_exchange(self, user_text, assistant_text):
        """Mirror a deterministic host-owned turn into an active Realtime session."""
        if self.closed or self.ws is None:
            return
        await self.ready.wait()
        async with self.delivery_lock:
            for role, content_type, value in (
                    ("user", "input_text", user_text),
                    ("assistant", "output_text", assistant_text)):
                await self.send({"type": "conversation.item.create", "item": {
                    "type": "message", "role": role,
                    "content": [{"type": content_type, "text": str(value)}],
                }})
        self.trace("host_exchange.injected")

    async def notify_task_result(self, item):
        notification_id = str(item.get("notification_id") or "")
        if not notification_id:
            raise ValueError("任务通知缺少 notification_id")
        if notification_id in self.pending_notifications:
            return
        self.pending_notifications.add(notification_id)
        await self.notifications.put(dict(item))

    async def deliver_notifications(self):
        while True:
            item = await self.notifications.get()
            notification_id = item["notification_id"]
            try:
                async with self.delivery_lock:
                    while (self.user_speaking or self.response_active or self.playing
                           or time.monotonic() - self.last_user_activity < 1.0):
                        await asyncio.sleep(0.1)
                    message = (
                        "[可信任务结果]\n"
                        f"状态：{item.get('outcome', 'completed')}\n"
                        f"结果：{item.get('summary', '')}\n"
                        "这是后台刚刚完成的任务结果，不是新的操作请求。"
                        "请只用一句简短自然的话把上述结果通知用户，不要再次调用run_task。"
                    )
                    await self.send({"type": "conversation.item.create", "item": {
                        # Realtime providers do not consistently use a newly
                        # appended system item as the immediate response turn.
                        # A synthetic user turn reliably grounds response.create;
                        # Product Session persistence still excludes this
                        # internal notification via response origin tracking.
                        "type": "message", "role": "user",
                        "content": [{"type": "input_text", "text": message}],
                    }})
                    await self._create_response({
                        "kind": "notification",
                        "notification_id": notification_id})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.pending_notifications.discard(notification_id)
                self._dispatch_notification_event(
                    "delivery_interrupted", notification_id=notification_id,
                    error=str(exc) or "notification_delivery_failed")

    async def deliver_results(self):
        while True:
            call_id, name, result = await self.results.get()
            async with self.delivery_lock:
                while (self.user_speaking or self.response_active or self.playing
                       or time.monotonic() - self.last_user_activity < 1.0):
                    await asyncio.sleep(0.1)
                await self.send({"type": "conversation.item.create", "item": {
                    "type": "function_call_output", "call_id": call_id,
                    "output": json.dumps(result, ensure_ascii=False)}})
                # run_task is asynchronous. Its originating response already
                # told the user that execution started, and the terminal
                # result is delivered through the notification channel. Asking
                # Qwen for another response here can race with a later user
                # turn, repeat stale task text, and even dispatch run_task a
                # second time against no Product Interaction.
                if name == "run_task" and result.get("status") == "accepted":
                    self.trace(
                        "tool.followup_suppressed", call_id=call_id,
                        name=name, status=result.get("status"))
                    continue
                await self._create_response({
                    "kind": "tool_result", "call_id": call_id})

    async def _create_response(self, origin):
        self.response_origins.append(dict(origin))
        self.response_active = True
        try:
            await self.send({"type": "response.create", "response": {
                "modalities": ["audio", "text"]}})
        except BaseException:
            if self.response_origins and self.response_origins[-1] == origin:
                self.response_origins.pop()
            raise

    async def guarded_delivery(self):
        try:
            await self.deliver_results()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.closed:
                self.emit("voice.error", error="结果回传中断：" + str(exc))
                await self.stop()

    async def guarded_notifications(self):
        try:
            await self.deliver_notifications()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.closed:
                self.emit("voice.error", error="任务提醒中断：" + str(exc))
                await self.stop()

    async def stop(self):
        self.closed = True
        if self.audio:
            self.audio.clear()
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()

    async def finish_pending_input(self, timeout=4.0):
        """Let server VAD finalize the current utterance before disconnecting.

        Live partial transcription is presentation-only. Closing the transport
        before the provider emits the final transcript would otherwise discard
        the user's request and prevent tool dispatch.
        """
        if not self.input_turn_pending:
            return True
        try:
            await asyncio.wait_for(self.input_turn_settled.wait(), timeout)
            return True
        except TimeoutError:
            self.trace("input.finalization_timeout", timeout_seconds=timeout)
            return False

    def _dispatch_notification_event(self, kind, **payload):
        if self.notification_event is None:
            return
        result = self.notification_event(kind, payload)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)
