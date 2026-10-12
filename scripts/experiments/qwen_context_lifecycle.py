"""AOQ 上验证 Query 尾部参考包的注入、删除与后续回复。

关闭麦克风和播放，仅发送合成文字；产生真实模型调用与费用。
使用启动桌宠的 Python 环境运行，--out 可保存原始非音频事件：
    python scripts/experiments/qwen_context_lifecycle.py --out /tmp/context-probe.jsonl

成功只证明服务端接受参考包与删除、第二轮可正常作答；不证明缓存命中，
也不证明旧参考中的事实未通过助手回答进入正常对话历史。
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
from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession  # noqa: E402


class ContextProbe(AoqRealtimeSession):
    def __init__(self, *args, log, **kwargs):
        super().__init__(*args, **kwargs)
        self.log = log
        self.observed = []
        self.finished_text = asyncio.Queue()

    async def send_session_update(self, config):
        await self.send({"type": "session.update", "session": {
            "modalities": ["text", "audio"], **config,
            "instructions": self.instructions, "tools": [],
            "turn_detection": {"type": "semantic_vad", "create_response": False},
        }})

    async def receive(self, event):
        kind = event.get("type", "")
        if kind not in {"response.audio.delta", "response.audio_transcript.delta",
                        "response.text.delta"}:
            self.log({"time": time.time(), "event": event})
        if kind in {"conversation.item.created", "conversation.item.deleted", "error"}:
            self.observed.append(event)
        # AOQ sometimes delays response.done beyond transcript/audio completion.
        # Keep response.done's actual usage in the log if it arrives; do not
        # invent usage from this shorter observation window.
        if kind in {"response.text.done", "response.audio_transcript.done"}:
            text = event.get("text") or event.get("transcript") or ""
            if text.strip():
                self.finished_text.put_nowait(text)
        await super().receive(event)

    async def wait_event(self, predicate, timeout):
        async with asyncio.timeout(timeout):
            while True:
                if error := next((e for e in self.observed if e["type"] == "error"), None):
                    raise RuntimeError(json.dumps(error["error"], ensure_ascii=False))
                if event := next((e for e in self.observed if predicate(e)), None):
                    return event
                await asyncio.sleep(0.05)


async def probe(out, timeout):
    settings = load_settings()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as stream:
        def log(record):
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()

        session = ContextProbe(
            lambda *a, **k: {"ok": False, "error": "实验不执行工具"},
            lambda *a, **k: None,
            model=settings.voice_model, key=settings.qwen_api_key,
            instructions="这是独立上下文协议测试。只回答问题，一句话，不调用工具。",
            workspace_id=settings.qwen_workspace_id, region=settings.qwen_region,
            sdk_dir=settings.aoq_sdk_dir,
            work_dir=out.parent / (out.stem + "-aoq"),
            microphone=False, playback=False, log=log,
        )
        runner = asyncio.create_task(session.run())
        try:
            await asyncio.wait_for(session.ready.wait(), timeout)
            await session.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{
                    "type": "input_text", "text": "请重复本轮参考中的临时代号。"}],
            }})
            await session.wait_event(lambda e: e["type"] == "conversation.item.created"
                                     and e.get("item", {}).get("role") == "user", timeout)
            requested_id = "item_probe_note"
            await session.send({"type": "conversation.item.create", "item": {
                "id": requested_id, "type": "message", "role": "system",
                "content": [{"type": "input_text", "text":
                             "仅本轮参考：临时代号是青柠七号。此项只服务当前Query。"}],
            }})
            created = await session.wait_event(
                lambda e: e["type"] == "conversation.item.created"
                and e.get("item", {}).get("role") == "system", timeout)
            actual_id = created["item"]["id"]
            await session._create_response({"kind": "user"})
            first = await asyncio.wait_for(session.finished_text.get(), timeout)
            async with asyncio.timeout(timeout):
                while session.response_active:
                    await asyncio.sleep(0.05)
            await session.send({"type": "conversation.item.delete", "item_id": actual_id})
            await session.wait_event(
                lambda e: e["type"] == "conversation.item.deleted"
                and e.get("item_id") == actual_id, timeout)
            await session.submit_text("新的问题：只回答一加一等于几。")
            second = await asyncio.wait_for(session.finished_text.get(), timeout)
            result = {
                "model": settings.voice_model, "transport": "aoq",
                "injection_after_user": "青柠七号" in first,
                "requested_item_id": requested_id, "provider_item_id": actual_id,
                "delete_acknowledged": True,
                "first_reply": first, "second_reply": second,
                "cache_hit_verified": False,
            }
            log({"result": result})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if not result["injection_after_user"]:
                raise RuntimeError("首轮回答未使用尾部参考包，检查原始事件")
        finally:
            await session.stop()
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=Path("/tmp/boxagent-context-probe") /
                        f"{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    parser.add_argument("--timeout", type=float, default=25)
    args = parser.parse_args()
    asyncio.run(probe(args.out, args.timeout))


if __name__ == "__main__":
    main()
