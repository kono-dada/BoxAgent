"""千问实时语音适配器；工具回传排队，接收循环始终可继续收听。"""

import asyncio
import base64
import contextlib
import json
import time

import websockets

from .audio import AudioIO
from .config import VOICE_MODEL, read_key

MODELS = {
    "qwen-audio-3.0-realtime-plus": {"voice": "longanqian", "turn_detection": {"type": "smart_turn"}},
    "qwen3.5-omni-flash-realtime": {"voice": "Ethan", "turn_detection": {
        "type": "semantic_vad", "threshold": 0.5, "silence_duration_ms": 800}},
}


def tool(name, description, properties=None):
    properties = properties or {}
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(properties)}}}


TOOLS = [
    tool("run_task", "把用户的自然语言目标委托给后台通用桌面操作 Agent，由它观察界面、自主决定操作并执行。可能需要几十秒到几分钟；调用前先简短语音回应，返回前不能假称完成。",
         {"goal": {"type": "string", "description": "完整保留用户希望完成的目标、目标应用及约束；不需要归类成预设任务或编写操作步骤。"}}),
    tool("cancel_task", "用户明确要求取消或停止后台任务时调用；一般插话、问问题不等于取消任务。"),
    tool("task_status", "查询后台任务进度或最近的真实结果。"),
]

INSTRUCTIONS = (
    "你是住在 Mac 桌面的 BoxAgent，用简短自然的中文和用户对话。"
    "用户要求操作电脑或应用时，调用 run_task，把用户目标与约束完整交给后台通用 Agent。"
    "把操作请求直接交给后台，根据工具实际反馈判断可执行性。"
    "后台自己发现应用、读界面、执行和判断结果；前台不要替它编造固定点击流程。"
    "关键交互：调用前先简短说你开始处理、用户可以继续聊天，再调用工具。通常需要几十秒到几分钟。"
    "不要在工具返回前假称目标已完成；blocked 或 failed 要如实解释。等待工具时仍可回应用户的其他话题。"
    "用户说取消任务时调用 cancel_task；用户仅仅插话不取消任务。工具失败或取消时如实报告，不能冒充成功。"
    "每次一般回答一到两句话，不要大段自我介绍。"
)


class QwenVoice:
    def __init__(self, handle_tool, emit, *, model=VOICE_MODEL, microphone=True, playback=True,
                 trace=None, audio_factory=AudioIO):
        self.handle_tool, self.emit = handle_tool, emit
        self.model, self.microphone, self.playback = model, microphone, playback
        self.audio_factory = audio_factory
        self.trace = trace or (lambda *_args, **_kwargs: None)
        self.ws = self.audio = None
        self.ready = asyncio.Event()
        self.queued_calls = []
        self.seen_calls = set()
        self.deliveries = set()
        self.results = asyncio.Queue()
        self.response_id = None
        self.response_active = self.user_speaking = self.playing = False
        self.cancelled_responses = set()
        self.text = ""
        self.closed = False
        self.last_user_activity = 0.0

    async def send(self, message):
        if self.closed or self.ws is None:
            raise ConnectionError("语音会话已关闭")
        await self.ws.send(json.dumps(message))

    def playback_changed(self, active):
        self.playing = active
        if not self.closed:
            self.emit("voice.playback", speaking=active)
            self.trace("playback", active=active)

    async def run(self):
        config = MODELS.get(self.model)
        if not config:
            raise ValueError("不支持的语音模型配置")
        key = read_key()
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
            async with websockets.connect(
                f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={self.model}",
                additional_headers={"Authorization": f"Bearer {key}"}, max_size=16 * 1024 * 1024,
                open_timeout=15, close_timeout=1, ping_interval=20, ping_timeout=20,
            ) as self.ws:
                await self.send({"type": "session.update", "session": {
                    "modalities": ["text", "audio"], **config, "instructions": INSTRUCTIONS, "tools": TOOLS}})
                workers.append(asyncio.create_task(self.guarded_delivery()))
                if self.microphone:
                    workers.append(asyncio.create_task(self.send_mic()))
                async for raw in self.ws:
                    await self.receive(json.loads(raw))
        finally:
            self.closed = True
            cleanup = asyncio.create_task(self.close_resources(workers))
            # 外层会话取消不能跳过设备关闭，否则原生音频回调可能活到 Python 退出之后。
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()

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
        if kind not in {"response.audio.delta", "response.audio_transcript.delta", "response.text.delta"}:
            self.trace(kind, **{key: value for key, value in event.items() if key in {"call_id", "name", "arguments", "transcript", "error"}})
        if kind == "session.updated":
            self.ready.set()
            self.emit("voice.ready", voice="ready", error="")
        elif kind == "response.created":
            self.response_active = True
            self.response_id = event.get("response", {}).get("id")
            self.text = ""
        elif kind == "input_audio_buffer.speech_started":
            self.user_speaking = True
            self.last_user_activity = time.monotonic()
            self.audio.clear()
            self.emit("voice.listening", user_speaking=True, speaking=False)
            if self.response_active and self.response_id:
                self.cancelled_responses.add(self.response_id)
                await self.send({"type": "response.cancel"})
        elif kind == "input_audio_buffer.speech_stopped":
            self.user_speaking = False
            self.last_user_activity = time.monotonic()
            self.emit("voice.input_stopped", user_speaking=False)
        elif kind == "conversation.item.input_audio_transcription.completed":
            self.emit("voice.user_text", user_text=event.get("transcript", ""))
        elif kind == "response.audio.delta":
            if not self.user_speaking and event.get("response_id") not in self.cancelled_responses:
                self.audio.append(event["delta"])
        elif kind in {"response.audio_transcript.delta", "response.text.delta"}:
            if event.get("response_id") not in self.cancelled_responses:
                self.text += event.get("delta", "")
                self.emit("voice.assistant_text", assistant_text=self.text)
        elif kind == "response.audio_transcript.done":
            if event.get("response_id") not in self.cancelled_responses:
                self.emit("voice.assistant_text", assistant_text=event.get("transcript", ""))
        elif kind == "response.function_call_arguments.done":
            call_id = event.get("call_id")
            if call_id and call_id not in self.seen_calls:
                self.seen_calls.add(call_id)
                self.queued_calls.append(event)
        elif kind == "response.done":
            self.response_active = False
            # 不在接收循环中等待工具；继续接收新一轮用户语音。
            calls, self.queued_calls = self.queued_calls, []
            for call in calls:
                task = asyncio.create_task(self.run_tool(call))
                self.deliveries.add(task)
                task.add_done_callback(self.deliveries.discard)
        elif kind == "error":
            error = event.get("error", {})
            message = error.get("message", "语音服务发生错误")
            # VAD 可能已经抢先取消，本地重复取消无须中断会话。
            if "no active response" not in message.lower() and "no response" not in message.lower():
                self.emit("voice.error", error=message)
            if not self.ready.is_set():
                raise RuntimeError(message)

    async def run_tool(self, call):
        try:
            result = await self.handle_tool(call.get("name"), call.get("arguments", "{}"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            result = {"status": "failed", "message": str(exc)}
        self.trace("tool.result", call_id=call["call_id"], result=result)
        await self.results.put((call["call_id"], result))

    async def deliver_results(self):
        while True:
            call_id, result = await self.results.get()
            while (self.user_speaking or self.response_active or self.playing
                   or time.monotonic() - self.last_user_activity < 1.0):
                await asyncio.sleep(0.1)
            await self.send({"type": "conversation.item.create", "item": {
                "type": "function_call_output", "call_id": call_id,
                "output": json.dumps(result, ensure_ascii=False)}})
            # 同一协程仲裁，防止两个后台结果同时触发 response.create。
            self.response_active = True
            await self.send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})

    async def guarded_delivery(self):
        try:
            await self.deliver_results()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self.closed:
                self.emit("voice.error", error="结果回传中断：" + str(exc))
                await self.stop()

    async def stop(self):
        self.closed = True
        if self.audio:
            self.audio.clear()
        if self.ws:
            with contextlib.suppress(Exception):
                await self.ws.close()
