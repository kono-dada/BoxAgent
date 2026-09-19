"""真实窗口验收：使用合成输入，避免测试过程打开用户麦克风。"""

import asyncio
import argparse
import json
import os
import time
import wave
from pathlib import Path

import AppKit as AK
from PyObjCTools import AppHelper

from boxagent.appearance.codex_pets import CodexPetsAppearance
from boxagent.config import DATA, DEFAULT_PET
from boxagent.desktop import Backend, Desktop
from boxagent.executor import CodexExecutor
from boxagent.runtime import Runtime
from boxagent.voice import QwenVoice


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-desktop", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--audio", type=Path, required=True, help="测试语音，16 kHz 单声道 PCM16 WAV")
    parser.add_argument("--focus-codex", action="store_true")
    args = parser.parse_args()
    with wave.open(str(args.audio)) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            parser.error("--audio 必须为 16 kHz 单声道 PCM16 WAV")
        pcm = wav.readframes(wav.getnframes())
    output = DATA / "desktop-smoke"
    output.mkdir(parents=True, exist_ok=True)
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    voice = None

    def make_voice(handle, emit):
        nonlocal voice
        voice = QwenVoice(handle, emit, microphone=False, playback=False)
        return voice

    backend = Backend(lambda publish: Runtime(publish,
        lambda task_id: CodexExecutor(task_id, auto_approve=False), make_voice))
    desktop = Desktop.alloc().init().configure(backend, CodexPetsAppearance(DEFAULT_PET))
    app.setDelegate_(desktop)
    result = {}

    def finish():
        desktop.applicationShouldTerminate_(app)
        app.stop_(None)

    def on_main(function, *args):
        future = asyncio.get_running_loop().create_future()
        def perform():
            def finish(value=None, error=None):
                if not future.done():
                    future.set_exception(error) if error else future.set_result(value)
            try:
                result = function(*args)
                backend.loop.call_soon_threadsafe(finish, result)
            except Exception as exc:
                backend.loop.call_soon_threadsafe(finish, None, exc)
        AppHelper.callAfter(perform)
        return future

    def capture(name):
        import subprocess
        subprocess.run(["screencapture", "-x", "-o", "-l", str(desktop.bubble.windowNumber()), str(output / f"{name}-bubble.png")], check=True)
        subprocess.run(["screencapture", "-x", "-o", "-l", str(desktop.pet.windowNumber()), str(output / f"{name}-pet.png")], check=True)

    def front_app():
        return AK.NSWorkspace.sharedWorkspace().frontmostApplication().bundleIdentifier()

    def focus_codex():
        candidates = AK.NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.openai.codex")
        if candidates:
            candidates[0].activateWithOptions_(AK.NSApplicationActivateIgnoringOtherApps)

    async def scenario():
        feed = None
        watcher = None
        foreground = []
        started = time.monotonic()
        try:
            await asyncio.sleep(1)
            if args.focus_codex:
                await on_main(focus_codex)
                await asyncio.sleep(.5)
            before = await on_main(front_app)
            result["hotkey_registered"] = await on_main(lambda: desktop.hotkey.registered)
            async def watch_front():
                while True:
                    current = await on_main(front_app)
                    if not foreground or foreground[-1]["app"] != current:
                        foreground.append({"elapsed": round(time.monotonic() - started, 3), "app": current,
                                           "task": backend.runtime.state.task, "progress": backend.runtime.state.task_text})
                    await asyncio.sleep(.2)
            watcher = asyncio.create_task(watch_front())
            await on_main(desktop.toggleBubble_, None)
            await on_main(desktop.toggleBubble_, None)
            result["bubble_toggles"] = await on_main(lambda: desktop.bubble.isVisible())
            await on_main(desktop.toggleMic_, None)
            while voice is None:
                await asyncio.sleep(.01)
            await asyncio.wait_for(voice.ready.wait(), 25)
            await on_main(capture, "listening")
            async def send():
                for index in range(0, len(pcm), 3200):
                    await voice.feed_audio(pcm[index:index + 3200].ljust(3200, b"\0"))
                    await asyncio.sleep(.1)
                while True:
                    await voice.feed_audio(bytes(3200))
                    await asyncio.sleep(.1)
            feed = asyncio.create_task(send())
            async with asyncio.timeout(60):
                while not backend.runtime.state.approval:
                    if backend.runtime.state.task == "failed":
                        raise RuntimeError(backend.runtime.state.task_text)
                    await asyncio.sleep(.1)
            await asyncio.sleep(.2)
            await on_main(capture, "approval")
            await on_main(desktop.allow_, None)
            await asyncio.wait_for(asyncio.shield(backend.runtime.job), 240)
            if backend.runtime.last_result.get("status") != "succeeded":
                raise RuntimeError("实际任务未完成：" + backend.runtime.state.task_text)
            await asyncio.sleep(7)
            await on_main(capture, "completed")
            result["task"] = backend.runtime.last_result
            result["front_before"] = before
            result["front_after"] = await on_main(front_app)
            result["front_unchanged"] = result["front_before"] == result["front_after"]
            result["foreground_samples_unique"] = foreground
            await on_main(desktop.toggleMic_, None)
            async with asyncio.timeout(10):
                while backend.runtime.state.voice != "off":
                    await asyncio.sleep(.1)
            result["voice_closed_cleanly"] = backend.runtime.state.voice == "off" and not backend.runtime.state.error
            result["pid"] = os.getpid()
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            if feed:
                feed.cancel()
                await asyncio.gather(feed, return_exceptions=True)
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            (output / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            AppHelper.callAfter(finish)

    AppHelper.callLater(1, lambda: backend.submit(scenario()))
    AppHelper.runEventLoop()
    if result.get("error"):
        raise SystemExit(result["error"])
