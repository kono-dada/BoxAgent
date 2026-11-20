"""Opt-in Qwen audio-output acceptance for a public game question and followups."""

import argparse
import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Protocol
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from boxagent.application.assistant import BoxAgentApplication
from boxagent.bootstrap.settings import Settings, load_settings
from boxagent.bootstrap.web import create_web_research
from boxagent.domain.conversation import ConversationService
from boxagent.infrastructure.persistence import JsonlSessionStore
from boxagent.infrastructure.runtimes.qwen.realtime import INSTRUCTIONS, QwenRealtimeSession
from boxagent.infrastructure.web.deepseek import DeepSeekWebProvider
from scripts.experiments.web_research_smoke import file_setting


class ToolHandler(Protocol):
    def __call__(self, name: str, arguments: object, metadata: dict[str, object] | None = None) -> Awaitable[dict[str, Any]]: ...


class Emitter(Protocol):
    def __call__(self, kind: str, **changes: object) -> None: ...


class NoDeviceAudio:
    """Collect provider audio without opening the microphone or speakers."""
    def __init__(self, changed: Callable[[bool], None], *, microphone: bool, playback: bool) -> None:
        pass

    def begin(self) -> None: pass
    def finish(self) -> None: pass
    def append(self, encoded: str) -> None: pass
    def clear(self) -> None: pass
    def close(self) -> None: pass


class VoiceProbe(QwenRealtimeSession):
    def __init__(self, handle: ToolHandler, emit: Emitter, **options: Any) -> None:
        super().__init__(handle, emit, **options)
        self.finished = asyncio.Event()
        self.answer = ""
        self.failure = ""
        self.answer_id = ""
        self.buffers: dict[str, bytearray] = {}
        self.audio_starts: dict[str, float] = {}

    async def receive(self, event: dict[str, Any]) -> None:
        # Provider JSON is a dynamic boundary; only the fields used by this
        # experiment are narrowed before saving evidence.
        response_id = event.get("response_id") or self.response_id
        if event.get("type") == "response.audio.delta" and isinstance(response_id, str):
            delta = event.get("delta")
            if isinstance(delta, str):
                self.audio_starts.setdefault(response_id, time.monotonic())
                self.buffers.setdefault(response_id, bytearray()).extend(base64.b64decode(delta))
        await super().receive(event)
        if event.get("type") == "error":
            self.failure = "provider_error"
            self.finished.set()
        if event.get("type") == "response.done":
            response = event.get("response")
            if not isinstance(response, dict):
                return
            output = response.get("output", [])
            has_call = isinstance(output, list) and any(
                isinstance(item, dict) and item.get("type") in {"function_call", "tool_call"} for item in output)
            if not has_call and isinstance(response.get("id"), str):
                identifier = response["id"]
                self.answer = self.response_transcripts.get(identifier, self.text)
                self.answer_id = identifier
                self.finished.set()


async def run(settings: Settings, output: Path) -> bool:
    if not settings.deepseek_api_key or not settings.qwen_api_key or not settings.qwen_workspace_id:
        raise ValueError("请配置 DeepSeek Key、Qwen Key 与对应 Workspace ID")
    output.mkdir(parents=True, exist_ok=True)
    voices: list[VoiceProbe] = []
    tools: list[dict[str, object]] = []
    requests: list[dict[str, object]] = []

    def factory(handle: ToolHandler, emit: Emitter, **options: Any) -> VoiceProbe:
        async def only_web(name: str, arguments: object, metadata: dict[str, object] | None = None) -> dict[str, Any]:
            if name not in {"search_web", "read_webpage"}:
                tools.append({"tool": name, "status": "blocked"})
                return {"status": "failed", "message": "只读实验不执行桌面操作"}
            result = await handle(name, arguments, metadata)
            tools.append({"tool": name, "status": result.get("status", ""),
                          "code": result.get("code", ""), "query": result.get("query", ""),
                          "read_source_count": result.get("read_source_count", 0),
                          "sources": result.get("sources", []), "pages": result.get("pages", [])})
            return result

        options.update(key=settings.qwen_api_key, model=settings.voice_model,
                       instructions=INSTRUCTIONS, audio_factory=NoDeviceAudio,
                       host_turn_control=True, websocket_proxy=None,
                       websocket_url=f"wss://{settings.qwen_workspace_id}.{settings.qwen_region}.maas.aliyuncs.com/api-ws/v1/realtime?model={settings.voice_model}")
        voice = VoiceProbe(only_web, emit, **options)
        voices.append(voice)
        return voice

    research = create_web_research(replace(settings, web_search_enabled=True))
    provider = research.provider
    if isinstance(provider, DeepSeekWebProvider):
        poster = provider.poster

        def observed_post(url: str, payload: Mapping[str, object], *, api_key: str, timeout: float) -> object:
            requests.append({"path": url.rsplit("/", 1)[-1], "query": payload.get("query", "")})
            return poster(url, payload, api_key=api_key, timeout=timeout)

        provider.poster = observed_post
    application = BoxAgentApplication(lambda _event: None, lambda *_args: None, factory,
        conversation_service=ConversationService(JsonlSessionStore(output / "sessions")),
        web_research=research)
    cases: list[dict[str, object]] = []
    scenarios = [
        ("guide", "我在玩《鸣潮》，打的是大世界野外普通形态的无冠者。帮我联网查一下闪避或打断时机，我正打着，只说两点，不要念网址。"),
        ("repeat", "再说一遍，更短一点。"),
        ("sources", "你刚才说的来源是哪？只说站点名字就好。"),
        ("clarify", "帮我联网查一下这个Boss怎么打，只说两点。"),
        ("wrong_form", "帮我查一下《鸣潮》周本无冠者的弹刀时机。"),
    ]
    await application.start()
    try:
        for name, question in scenarios:
            if name in {"clarify", "wrong_form"}:
                await application.create_session("指代不清验收")
            if voices:
                voices[-1].finished.clear()
                voices[-1].answer = ""
            first_tool = len(tools)
            first_request = len(requests)
            started = time.monotonic()
            await application.submit_text(question)
            voice = voices[-1]
            await asyncio.wait_for(voice.finished.wait(), 60)
            pcm = voice.buffers.get(voice.answer_id, bytearray())
            filename = output / f"{name}.wav"
            with wave.open(str(filename), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(pcm)
            actual_tools = tools[first_tool:]
            urls_spoken = bool(re.search(r"https?://|www\.", voice.answer, re.IGNORECASE))
            passed = bool(voice.answer and pcm and not voice.failure and not urls_spoken and len(voice.answer) <= 80 and len(pcm) / 48000 <= 15)
            if name == "guide":
                passed = passed and any(item.get("read_source_count", 0) for item in actual_tools)
            else:
                passed = passed and len(requests) == first_request
                if name == "wrong_form":
                    passed = passed and "无妄者" in voice.answer and any(char in voice.answer for char in "？?")
                if name == "clarify":
                    passed = passed and any(char in voice.answer for char in "？?")
            cases.append({"case": name, "question": question, "answer": voice.answer,
                          "characters": len(voice.answer), "audio_seconds": round(len(pcm) / 48000, 2),
                          "elapsed_seconds": round(time.monotonic() - started, 2),
                          "first_answer_audio_seconds": round(voice.audio_starts.get(voice.answer_id, started) - started, 2),
                          "tools": actual_tools, "urls_spoken": urls_spoken,
                          "web_requests": requests[first_request:],
                          "passed": passed, "audio": str(filename.resolve())})
            await asyncio.sleep(.2)
    finally:
        await application.close()
    (output / "evidence.json").write_text(json.dumps({"cases": cases, "microphone": False,
        "speaker_playback": False, "desktop_actions": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    for case in cases:
        print(json.dumps({key: value for key, value in case.items() if key != "tools"}, ensure_ascii=False))
    return all(case["passed"] for case in cases)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".runtime/web-research/voice"))
    args = parser.parse_args()
    if not args.live:
        parser.error("使用 --live 才会调用真实模型和搜索；只发送公开验收问题，不开启麦克风或扬声器")
    settings = load_settings()
    if args.env_file:
        settings = replace(settings,
            deepseek_api_key=file_setting(args.env_file, "DEEPSEEK_API_KEY"),
            deepseek_base_url=file_setting(args.env_file, "BOXAGENT_DEEPSEEK_BASE_URL") or settings.deepseek_base_url,
            qwen_api_key=file_setting(args.env_file, "DASHSCOPE_API_KEY"),
            qwen_workspace_id=file_setting(args.env_file, "BOXAGENT_DASHSCOPE_WORKSPACE_ID"),
            qwen_region=file_setting(args.env_file, "BOXAGENT_QWEN_REGION") or settings.qwen_region)
    raise SystemExit(0 if asyncio.run(run(settings, args.output)) else 1)


if __name__ == "__main__":
    main()
