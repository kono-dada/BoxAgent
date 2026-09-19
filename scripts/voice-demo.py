# /// script
# requires-python = ">=3.11"
# dependencies = ["pyaudio>=0.2.14", "websockets>=15,<17"]
# ///

import argparse
import asyncio
import base64
import json
import os
import threading
from pathlib import Path

import pyaudio
import websockets

from calculator import TOOL, run_calculator


MODELS = {
    "qwen-audio-3.0-realtime-plus": {
        "voice": "longanqian",
        "turn_detection": {"type": "smart_turn"},
    },
    "qwen3.5-omni-flash-realtime": {
        "voice": "Ethan",
        "turn_detection": {
            "type": "semantic_vad",
            "threshold": 0.5,
            "silence_duration_ms": 800,
        },
    },
}


def read_key():
    if value := os.environ.get("DASHSCOPE_API_KEY"):
        return value
    path = Path(__file__).resolve().parents[1] / ".env.local"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("DASHSCOPE_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("未找到 DASHSCOPE_API_KEY；请先运行 zsh scripts/set-key.zsh")


class Player:
    def __init__(self, audio):
        self.buffer = bytearray()
        self.lock = threading.Lock()
        self.stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True,
            frames_per_buffer=1200,
            stream_callback=self.callback,
        )

    def callback(self, _input, frame_count, _time_info, _status):
        count = frame_count * 2
        with self.lock:
            chunk = bytes(self.buffer[:count])
            del self.buffer[:count]
        return (chunk.ljust(count, b"\x00"), pyaudio.paContinue)

    def append(self, encoded):
        with self.lock:
            self.buffer.extend(base64.b64decode(encoded))

    def clear(self):
        with self.lock:
            self.buffer.clear()

    def close(self):
        self.stream.stop_stream()
        self.stream.close()


async def main(model):
    key = read_key()
    url = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={model}"
    config = MODELS[model]
    audio = pyaudio.PyAudio()
    mic = None
    player = None
    pending_tool_calls = []
    try:
        mic = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=16000,
            input=True,
            frames_per_buffer=1600,
        )
        player = Player(audio)
        async with websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {key}"},
            max_size=16 * 1024 * 1024,
        ) as ws:
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "voice": config["voice"],
                    "instructions": (
                        "你是桌面语音助手，用自然、简洁的中文语音回答。需要算数时必须调用 calculate 工具。"
                        "如果工具描述表明结果需要等待，先用一句简短语音告诉用户你将开始处理、预计要等多久；"
                        "先把这句提醒说出来，再发起工具调用，不要等工具返回后才第一次开口。"
                        "工具返回之前不要猜测答案，也不要声称任务已经完成。拿到工具结果后，再用语音报告结果。"
                    ),
                    "turn_detection": config["turn_detection"],
                    "tools": [TOOL],
                },
            }))
            print(f"已连接 {model}。戴耳机直接说话；可要求计算并在回复时插话。按 Ctrl+C 退出。", flush=True)

            async def send_mic():
                while True:
                    chunk = await asyncio.to_thread(mic.read, 1600, exception_on_overflow=False)
                    await ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    }))

            async def receive():
                async def deliver_calculator_results(calls):
                    for call_id, name, arguments, requested_at in calls:
                        await asyncio.sleep(max(0, 7 - (asyncio.get_running_loop().time() - requested_at)))
                        result = run_calculator(arguments) if name == "calculate" else json.dumps({"error": "未知工具"}, ensure_ascii=False)
                        elapsed = asyncio.get_running_loop().time() - requested_at
                        print(f"[计算器] {elapsed:.2f} 秒后返回：{result}", flush=True)
                        await ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {"type": "function_call_output", "call_id": call_id, "output": result},
                        }))
                    await ws.send(json.dumps({
                        "type": "response.create",
                        "response": {"modalities": ["audio", "text"]},
                    }))

                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "response.audio.delta":
                        player.append(event["delta"])
                    elif kind == "input_audio_buffer.speech_started":
                        player.clear()
                        print("\n[检测到你说话；清空旧回复]", flush=True)
                    elif kind == "conversation.item.input_audio_transcription.completed":
                        print(f"\n你：{event.get('transcript', '')}", flush=True)
                    elif kind == "response.audio_transcript.done":
                        print(f"千问：{event.get('transcript', '')}", flush=True)
                    elif kind == "response.function_call_arguments.done":
                        if event.get("call_id"):
                            requested_at = asyncio.get_running_loop().time()
                            if event.get("name") == "calculate":
                                arguments = event.get("arguments", "")
                                print("[计算器] 已收到调用；约 7 秒后才返回结果。", flush=True)
                            else:
                                arguments = json.dumps({"expression": ""})
                                print(f"[拒绝未知工具] {event.get('name')}", flush=True)
                            pending_tool_calls.append((event["call_id"], event.get("name"), arguments, requested_at))
                    elif kind == "response.done":
                        if pending_tool_calls:
                            calls = pending_tool_calls.copy()
                            pending_tool_calls.clear()
                            asyncio.create_task(deliver_calculator_results(calls))
                    elif kind == "error":
                        print(f"服务端错误：{event.get('error', {}).get('message', event)}", flush=True)

            await asyncio.gather(send_mic(), receive())
    finally:
        if mic:
            mic.stop_stream()
            mic.close()
        if player:
            player.close()
        audio.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="千问实时双向语音最小演示")
    parser.add_argument("--model", choices=MODELS, default="qwen-audio-3.0-realtime-plus")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.model))
    except KeyboardInterrupt:
        print("\n已退出。")
