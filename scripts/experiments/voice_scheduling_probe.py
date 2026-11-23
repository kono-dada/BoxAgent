"""Measure scheduled LoL playback with real Qwen and the local output device."""

import argparse
import asyncio
import json
import statistics
import time

from boxagent.bootstrap.settings import Settings, load_settings
from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession
from boxagent.infrastructure.runtimes.qwen.realtime import LolSpeech, QwenRealtimeSession


async def run(settings: Settings, transport: str, samples: int, *, paired: bool = False) -> None:
    marks: dict[str, float] = {}
    started = asyncio.Event()
    playback_order: list[str] = []
    playback_changed = asyncio.Event()

    def emit(event: str, **data: object) -> None:
        if event == "voice.playback" and data.get("speaking"):
            marks.setdefault("device_callback_ms", time.perf_counter())
        if event == "voice.error":
            print(json.dumps({"event": "voice.error"}), flush=True)

    def trace(event: str, **data: object) -> None:
        request = data.get("request")
        if (
            event == "client.request"
            and isinstance(request, dict)
            and request.get("type") == "response.create"
        ):
            marks.setdefault("response_create_ms", time.perf_counter())

    async def deny(*_args: object) -> dict[str, object]:
        raise RuntimeError("The probe only sends synthetic announcement text")

    def record_start(source: str) -> None:
        playback_order.append(source)
        playback_changed.set()

    def notification_event(event: str, data: dict[str, object]) -> None:
        if event == "playback_started" and data.get("notification_id") == "synthetic-wechat":
            record_start("wechat")

    def lol_started() -> None:
        marks.setdefault("lol_receipt_ms", time.perf_counter())
        record_start("lol")
        started.set()

    def new_speech() -> LolSpeech:
        deadline = time.monotonic() + 3.0
        return LolSpeech("漂亮，这波打得好！", lambda: time.monotonic() < deadline, lol_started)

    def schedule_lol(speech: LolSpeech) -> bool:
        return voice.schedule_lol(speech.line, still_current=speech.still_current,
                                  playback_started=speech.playback_started)

    if transport == "aoq":
        voice = AoqRealtimeSession(
            deny,
            emit,
            key=settings.qwen_api_key,
            model=settings.voice_model,
            host_turn_control=True,
            microphone=False,
            playback=True,
            trace=trace,
            notification_event=notification_event,
            workspace_id=settings.qwen_workspace_id,
            region=settings.qwen_region,
            sdk_dir=settings.aoq_sdk_dir,
            work_dir=settings.aoq_work_dir,
        )
    else:
        url = (
            f"wss://{settings.qwen_workspace_id}.{settings.qwen_region}.maas.aliyuncs.com"
            f"/api-ws/v1/realtime?model={settings.voice_model}"
        )
        voice = QwenRealtimeSession(
            deny,
            emit,
            key=settings.qwen_api_key,
            model=settings.voice_model,
            host_turn_control=True,
            microphone=False,
            playback=True,
            trace=trace,
            notification_event=notification_event,
            websocket_url=url,
            websocket_proxy=None,
        )

    async def wait_idle() -> None:
        async with asyncio.timeout(15):
            while voice._delivery_busy():
                await asyncio.sleep(.025)

    print(
        json.dumps(
            {
                "transport": transport,
                "model": settings.voice_model,
                "samples": samples,
                "paired": paired,
                "warmups": 1,
                "microphone": False,
                "output": "system default",
            }
        ),
        flush=True,
    )
    worker = asyncio.create_task(voice.run())
    measurements: dict[str, list[dict[str, float]]] = {"scheduled": []}
    if paired:
        measurements["direct"] = []
    try:
        await asyncio.wait_for(voice.ready.wait(), 20)
        for index in range(samples + 1):
            modes = list(measurements) if index % 2 else list(reversed(measurements))
            for mode in modes:
                await wait_idle()
                marks.clear()
                started.clear()
                begin = time.perf_counter()
                speech = new_speech()
                if mode == "direct":
                    # Same Qwen handshake and playback path, bypassing scheduling.
                    async with voice.delivery_lock:
                        accepted = await voice._deliver_lol(speech)
                else:
                    accepted = schedule_lol(speech)
                if not accepted:
                    raise RuntimeError("LoL scheduling failed in an idle session")
                await asyncio.wait_for(started.wait(), 10)
                row = {
                    key: round((timestamp - begin) * 1000, 3) for key, timestamp in marks.items()
                }
                print(
                    json.dumps({"i": index, "mode": mode, "warmup": index == 0, **row}), flush=True
                )
                if index:
                    measurements[mode].append(row)

        await wait_idle()
        playback_order.clear()
        await voice.notify_task_result(
            {
                "notification_id": "synthetic-wechat",
                "kind": "external_event",
                "summary": "测试消息。小林说，晚上七点见。",
            }
        )
        if not schedule_lol(new_speech()):
            raise RuntimeError("LoL scheduling failed with concurrent WeChat")
        async with asyncio.timeout(15):
            while len(playback_order) < 2:
                playback_changed.clear()
                await playback_changed.wait()
        print(json.dumps({"case": "both_ready", "device_receipts": playback_order}), flush=True)
        if playback_order != ["lol", "wechat"]:
            raise AssertionError("Unexpected playback order")
        await wait_idle()
    finally:
        await voice.stop()
        await worker
    for mode, rows in measurements.items():
        fields = sorted(set.intersection(*(set(row) for row in rows)))
        summary = {key: {"p50": statistics.median(row[key] for row in rows),
                         "max": max(row[key] for row in rows)} for key in fields}
        print(json.dumps({"mode": mode, "summary": summary}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Send synthetic text to Qwen and enable device output"
    )
    parser.add_argument("--transport", choices=("aoq", "websocket"), default="aoq")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument(
        "--paired", action="store_true", help="Alternate scheduled and direct delivery"
    )
    args = parser.parse_args()
    if not args.live or args.samples < 1:
        parser.error("Pass --live and use a positive sample count")
    settings = load_settings()
    if not settings.qwen_api_key or not settings.qwen_workspace_id:
        parser.error("Configure the local Qwen key and Workspace ID")
    asyncio.run(run(settings, args.transport, args.samples, paired=args.paired))


if __name__ == "__main__":
    main()
