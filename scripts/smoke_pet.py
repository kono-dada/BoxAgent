"""通过自然语言目标验证通用 Agent；可附带 16 kHz WAV 作为语音输入。"""

import argparse
import asyncio
import base64
import contextlib
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boxagent.audio import AudioIO
from boxagent.config import DATA
from boxagent.executor import CodexExecutor
from boxagent.runtime import Runtime
from boxagent.voice import QwenVoice


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--goal", default="打开计算器，计算137+248，并检查结果")
    parser.add_argument("--followup", type=Path)
    parser.add_argument("--allow-app", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true", help="只观察语音委托内容，不启动桌面执行器")
    parser.add_argument("--cancel-after", type=float)
    parser.add_argument("--cancel-after-action", type=int)
    args = parser.parse_args()
    if not args.allow_app and not args.dry_run:
        parser.error("真实系统测试须用 --allow-app 指定允许的应用名称或标识，可重复")
    output = DATA / "smoke" / time.strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    started = time.monotonic()
    events = []
    generated_audio = bytearray()

    def log(kind, **data):
        event = {"elapsed": round(time.monotonic() - started, 3), "kind": kind, **data}
        events.append(event)
        with (output / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        if kind in {"task.accepted", "task.succeeded", "task.failed", "task.cancelled", "tool.result", "error",
                    "conversation.item.input_audio_transcription.completed", "response.audio_transcript.done"}:
            print(json.dumps(event, ensure_ascii=False), flush=True)

    def publish(event):
        state = event["state"]
        log(event["type"], task=state["task"], task_text=state["task_text"], voice=state["voice"], speaking=state["speaking"])

    class RecordingAudio(AudioIO):
        def append(self, encoded):
            generated_audio.extend(base64.b64decode(encoded))
            super().append(encoded)

    voice = None

    def make_voice(handler, emit):
        nonlocal voice
        voice = QwenVoice(handler, emit, microphone=False, playback=False, trace=log, audio_factory=RecordingAudio)
        return voice

    class ObserveExecutor:
        actions = 0
        process = None
        def __init__(self, task_id):
            pass
        async def run(self, goal, progress, approve):
            log("delegation.observed", goal=goal)
            return {"outcome": "blocked", "summary": "语音委托已接收；本次只测试委托，不执行桌面操作。"}
        async def cancel(self):
            pass

    # 验收脚本必须经过下方的授权回调，不能沿用产品的默认自动授权。
    runtime = Runtime(publish, ObserveExecutor if args.dry_run else
                      lambda task_id: CodexExecutor(task_id, auto_approve=False), make_voice)
    async def approve(params):
        app = params.get("app", "")
        allowed = any(app.casefold() == value.casefold() or Path(app).stem.casefold() == value.casefold()
                      for value in args.allow_app)
        log("approval", app=app, allowed=allowed, message=params.get("message", ""))
        return allowed
    runtime.approve = approve
    pump = None
    try:
        if args.audio:
            await runtime.toggle_voice()
            ready_task = asyncio.create_task(voice.ready.wait())
            done, _ = await asyncio.wait([ready_task, runtime.voice_task], timeout=25, return_when=asyncio.FIRST_COMPLETED)
            if ready_task not in done:
                ready_task.cancel()
                raise RuntimeError("语音未就绪：" + runtime.state.error)
            queue = asyncio.Queue()

            async def feed():
                while True:
                    chunk = queue.get_nowait() if not queue.empty() else bytes(3200)
                    await voice.feed_audio(chunk)
                    await asyncio.sleep(.1)

            def enqueue(path):
                with wave.open(str(path)) as wav:
                    assert wav.getframerate() == 16000 and wav.getnchannels() == 1 and wav.getsampwidth() == 2
                    while chunk := wav.readframes(1600):
                        queue.put_nowait(chunk.ljust(3200, b"\0"))

            pump = asyncio.create_task(feed())
            enqueue(args.audio)
            async with asyncio.timeout(45):
                while not runtime.job:
                    await asyncio.sleep(.2)
            if args.followup:
                await asyncio.sleep(4)
                enqueue(args.followup)
        else:
            caller = asyncio.create_task(runtime.handle_tool("run_task", {"goal": args.goal}))
            await asyncio.sleep(.1)
        if args.cancel_after is not None:
            await asyncio.sleep(args.cancel_after)
            await runtime.cancel_task()
        if args.cancel_after_action is not None:
            async with asyncio.timeout(60):
                while runtime.executor.actions < args.cancel_after_action and not runtime.job.done():
                    await asyncio.sleep(.05)
            await runtime.cancel_task()
        await asyncio.wait_for(asyncio.shield(runtime.job), 340)
        if voice:
            await asyncio.sleep(15)
        summary = {"result": runtime.last_result, "elapsed": round(time.monotonic() - started, 3),
                   "task_id": runtime.state.task_id, "generated_audio_bytes": len(generated_audio),
                   "actions": runtime.executor.actions, "subprocess_stopped": runtime.executor.process is None or runtime.executor.process.returncode is not None}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if runtime.last_result["status"] not in {"succeeded", "cancelled"} and not args.dry_run:
            raise RuntimeError("实际任务未通过")
    finally:
        if pump:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
        await runtime.close()
        if generated_audio:
            with wave.open(str(output / "generated-replies.wav"), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(generated_audio)


if __name__ == "__main__":
    asyncio.run(main())
