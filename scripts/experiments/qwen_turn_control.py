"""REQ-04 实验：AOQ 传输下，宿主能否接管“何时回答”。

两种方案（只测 AOQ：WebSocket 没有背景降噪和回声消除，不作为产品路径）：

A  保留 semantic_vad，在 turn_detection 里加 ``create_response: false``。
   支持的话：用户说完后服务端只断句、出 ASR，不自动开口。
B  Manual 模式：``turn_detection: null``。宿主自己发
   ``input_audio_buffer.commit`` 和 ``response.create``。文档说 WebRTC 只支持
   VAD 模式，AOQ 走 WebRTC 端点，预期失败，但要实测确认。

每种方案连上 Qwen 后，请对着麦克风说 N 句短话（例如“帮我打开计算器”“你好”），
每句之间停顿 3 秒以上。脚本不主动发 response.create 之外的任何业务请求，
只记录每一轮的事件时序：

    speech_stopped → committed → ASR completed → response.created

判定：
    A 生效 ⇔ ASR completed 之后 ``--hold`` 秒内没有 response.created；
              宿主随后发 response.create，能正常拿到回答。
    B 生效 ⇔ session.updated 成功，宿主 commit 后能拿到 ASR，response.create 后能回答。

用法（会产生真实调用与费用；需要麦克风权限）：
    # 先退出正在运行的 BoxAgent，避免两个会话同时占用麦克风
    /tmp/uvcache/environments-v2/pet-*/bin/python3 \
        scripts/experiments/qwen_turn_control.py --plan A --turns 5

结果写入 /tmp/req04/<plan>-<时间>.jsonl（含 ASR 原文，不要提交）。
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from boxagent.bootstrap.settings import load_settings  # noqa: E402
from boxagent.infrastructure.runtimes.qwen import realtime  # noqa: E402
from boxagent.infrastructure.runtimes.qwen.aoq import (  # noqa: E402
    AoqRealtimeSession,
    aoq_preflight,
)

PLANS = {
    "A": {"type": "semantic_vad", "threshold": 0.5, "silence_duration_ms": 1500,
          "create_response": False},
    "B": None,
}
WATCH = {
    "session.updated", "error",
    "input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
    "input_audio_buffer.committed",
    "conversation.item.input_audio_transcription.completed",
    "response.created", "response.done", "response.audio_transcript.done",
}


class ProbeSession(AoqRealtimeSession):
    """生产 AOQ 会话，只替换 turn_detection，并在 ASR 定稿后由宿主决定何时回答。"""

    def __init__(self, *args, plan, hold, log, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan, self.hold, self.log = plan, hold, log
        self.turn = None
        self.turns = []

    async def send_session_update(self, config):
        config = {**config, "turn_detection": PLANS[self.plan]}
        await super().send_session_update(config)

    def mark(self, name, **extra):
        now = time.monotonic()
        self.log({"t": time.time(), "event": name, **extra})
        if name == "input_audio_buffer.speech_stopped" or self.turn is None:
            self.turn = {"t0": now, "events": {}}
            self.turns.append(self.turn)
        self.turn["events"].setdefault(name, round((now - self.turn["t0"]) * 1000))

    async def receive(self, event):
        kind = event.get("type")
        if kind in WATCH:
            extra = {}
            if kind == "error":
                extra["error"] = event.get("error")
            if kind.endswith("transcription.completed") or kind.endswith("transcript.done"):
                extra["text"] = event.get("transcript", "")
            self.mark(kind, **extra)
        if self.plan == "B" and kind == "input_audio_buffer.speech_stopped":
            await self.send({"type": "input_audio_buffer.commit"})
            self.mark("host.commit")
        await super().receive(event)
        if kind == "conversation.item.input_audio_transcription.completed":
            asyncio.get_running_loop().create_task(self.answer_after_hold())

    async def answer_after_hold(self):
        # 观察窗口：若服务端仍自动开口，response.created 会在这段时间里出现。
        await asyncio.sleep(self.hold)
        if "response.created" not in self.turn["events"]:
            self.mark("host.response_create")
            await self._create_response({"kind": "user"})


def verdict(turns, plan):
    rows = []
    for turn in turns:
        events = turn["events"]
        asr = events.get("conversation.item.input_audio_transcription.completed")
        created = events.get("response.created")
        host = events.get("host.response_create")
        if asr is None:
            rows.append(("no-asr", events))
            continue
        if host is not None and created is not None and created > host:
            rows.append(("host-controlled", events))
        elif created is not None and created <= asr + 50:
            rows.append(("auto-early", events))
        elif created is not None and host is None:
            rows.append(("auto-late", events))
        else:
            rows.append(("no-response", events))
    return rows


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", choices=sorted(PLANS), required=True)
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--hold", type=float, default=3.0,
                        help="ASR 定稿后等待多久再由宿主发 response.create（秒）")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    settings = load_settings()
    ok, reason = aoq_preflight(sdk_dir=settings.aoq_sdk_dir,
                               workspace_id=settings.qwen_workspace_id)
    if not ok:
        sys.exit(f"AOQ 不可用：{reason}")
    out_dir = Path("/tmp/req04")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.plan}-{time.strftime('%H%M%S')}.jsonl"
    stream = out.open("w", encoding="utf-8")

    def log(record):
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        if record["event"] in {"session.updated", "error", "host.response_create",
                               "conversation.item.input_audio_transcription.completed",
                               "response.created"}:
            print(record["event"], record.get("text") or record.get("error") or "", flush=True)

    session = ProbeSession(
        lambda *a, **k: {"ok": False, "error": "实验中不执行工具"},
        lambda *a, **k: None,
        model=settings.voice_model, key=settings.qwen_api_key,
        instructions=realtime.INSTRUCTIONS, trace=lambda *a, **k: None,
        workspace_id=settings.qwen_workspace_id, region=settings.qwen_region,
        sdk_dir=settings.aoq_sdk_dir, work_dir=settings.aoq_work_dir,
        plan=args.plan, hold=args.hold, log=log)
    print(f"方案 {args.plan}：{json.dumps(PLANS[args.plan], ensure_ascii=False)}；"
          f"请说 {args.turns} 句短话。", flush=True)
    runner = asyncio.create_task(session.run())
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline and not runner.done():
            done = sum("response.done" in t["events"] for t in session.turns)
            if done >= args.turns:
                break
            await asyncio.sleep(0.5)
    finally:
        await session.stop()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        stream.close()
    print("\n结果：", out)
    for label, events in verdict(session.turns, args.plan):
        print(f"  {label:16} {events}")


if __name__ == "__main__":
    asyncio.run(main())
