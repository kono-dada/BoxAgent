"""Paired live Qwen intent acceptance; all tools are stubbed, no desktop actions.

Run with the desktop Python environment (AOQ/PyObjC installed). The checked-in
cases are synthetic. A and B use the same continuous, initially empty session
and the same ordered inputs; only the JEV reference differs. Provider routing
and the inherited host prose fallback are measured separately. Latency includes
production TurnPreparer. This is a bounded acceptance batch, not an estimate of
real-user population error rates or cache hits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from boxagent.application.turn_preparation import TurnPreparer
from boxagent.bootstrap.memory import create_intent_reference
from boxagent.bootstrap.settings import load_settings
from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession

CASES = [
    ("你好，你叫什么名字？", False),
    ("今天忙了一整天，好累。", False),
    ("帮我打开计算器。", True),
    ("我喜欢听轻音乐放松。", False),
    ("给我播放轻音乐。", True),
    ("别放音乐了，我准备睡觉，晚安。", True),
    ("晚安，我去睡觉了。", False),
    ("打开浏览器搜索今天的 AI 新闻。", True),
    ("你觉得 AI 会怎样影响我们的生活？", False),
    ("把音乐暂停一下。", True),
    ("我不太喜欢这种音乐。", False),
    ("帮我收藏当前这首歌。", True),
    ("取消当前歌曲的收藏。", True),
    ("收藏音乐和下载音乐有什么区别？", False),
    ("请把音乐应用最小化。", True),
    ("讲一个短一点的冷笑话。", False),
    ("在计算器里算一下 7 乘 6，检查屏幕上的结果。", True),
    ("7 乘 6 等于多少？直接告诉我。", False),
    ("帮我看看现在屏幕上有什么。", True),
    ("截图可能包含什么隐私？", False),
    ("用浏览器打开知乎人工智能话题。", True),
    ("怎么在知乎查找好文章？告诉我步骤即可，不要操作。", False),
    ("我想给你取名小芽，可以吗？", False),
    ("我刚才给你取的名字是什么？", False),
    ("我现在更喜欢红茶，之前的乌龙茶偏好过时了。", False),
    ("根据我刚才的更正，我现在喜欢什么茶？", False),
    ("帮我关闭计算器。", True),
    ("听歌是个不错的主意，但现在不用帮我放。", False),
    ("请打开备忘录，创建标题为测试的小记事。", True),
    ("我觉得你刚才的回复有点生硬，能自然一点吗？", False),
    ("请用浏览器搜索 macOS 的官方帮助页面。", True),
    ("不要启动任何任务，我只想和你聊一会儿。", False),
]


class SyntheticMemory:
    async def context_packet(self, query: str, *, session_id: str, top_k: int) -> str:
        return "用户之前喜欢乌龙茶；以用户当前更正为准。"


async def run_arm(arm: str, out: Path, limit: int) -> list[dict[str, Any]]:
    settings = load_settings()
    intent = create_intent_reference(settings) if arm == "B" else None
    preparer = TurnPreparer(SyntheticMemory(), intent=intent,
                            environment=lambda: "当前为 macOS；没有当前应用、歌曲或执行结果的观测。")
    await preparer.start()
    if intent:
        await intent.start()
        # SDK process start is startup work, not part of the per-query budget.
        if intent.prewarm:
            await asyncio.wait_for(asyncio.shield(intent.prewarm), 20)
    current: dict[str, Any] = {}
    settled = asyncio.Event()

    def first_output() -> None:
        current.setdefault("first_output_ms", round((time.perf_counter() - current["start"]) * 1000, 1))

    def emit(kind: str, **data: Any) -> None:
        if kind == "voice.assistant_text" and data.get("assistant_text"):
            first_output()
        if kind == "voice.error":
            current.setdefault("errors", []).append(data.get("error"))

    def trace(kind: str, **data: Any) -> None:
        if kind == "response.created" and "start" in current:
            current.setdefault("response_created_ms", round((time.perf_counter() - current["start"]) * 1000, 1))

    async def tool(name: str, arguments: str, context: dict[str, Any]) -> dict[str, Any]:
        first_output()
        implicit = str(context.get("call_id", "")).startswith("implicit-run-task:")
        current.setdefault("tools", []).append({"name": name, "implicit": implicit})
        if name == "run_task":
            return {"status": "accepted", "task_id": "synthetic-no-execution",
                    "message": "仅接受测试请求；未执行电脑操作，尚无结果。"}
        return {"status": "blocked", "message": "验收工具未执行，没有真实结果。"}

    async def record(kind: str, payload: dict[str, Any]) -> None:
        if kind == "assistant_final":
            current.setdefault("replies", []).append(payload.get("transcript", ""))
        if kind == "response_done":
            settled.set()

    session = AoqRealtimeSession(tool, emit, trace=trace,
        model=settings.voice_model, key=settings.qwen_api_key,
        workspace_id=settings.qwen_workspace_id, region=settings.qwen_region,
        sdk_dir=settings.aoq_sdk_dir, work_dir=out / arm,
        host_turn_control=True, microphone=False, playback=False,
        product_session_id="ses_intent" + arm, conversation_event=record)
    runner = asyncio.create_task(session.run())
    results: list[dict[str, Any]] = []
    try:
        await asyncio.wait_for(session.ready.wait(), 25)
        for index, (query, action) in enumerate(CASES[:limit]):
            settled.clear()
            current = {"arm": arm, "index": index, "query": query, "action": action,
                       "start": time.perf_counter(), "tools": [], "replies": []}
            packet, preparation = await preparer.prepare(query, session_id="ses_intent" + arm,
                                                        query_id=str(index))
            current["preparation"] = preparation
            await session.submit_text(query, reference=packet, query_id=str(index))
            await asyncio.wait_for(settled.wait(), 30)
            async with asyncio.timeout(15):
                while session.response_active or not session.results.empty() or session.deliveries:
                    await asyncio.sleep(.05)
            await asyncio.sleep(.15)
            current.pop("start")
            current["provider_run_task"] = any(t["name"] == "run_task" and not t["implicit"] for t in current["tools"])
            current["host_fallback"] = any(t["name"] == "run_task" and t["implicit"] for t in current["tools"])
            text = " ".join(current["replies"])
            # Conservative, inspectable lexical flag; keep text for human audit.
            current["unsupported_completion_flag"] = bool(re.search(
                r"(?:已经|已)(?:帮你|为你)?(?:打开|关闭|播放|暂停|收藏|取消收藏|创建|搜索|最小化|执行)", text))
            results.append(dict(current))
            with (out / "cases.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(current, ensure_ascii=False) + "\n")
            print(json.dumps({"arm": arm, "case": index, "provider_call": current["provider_run_task"],
                              "fallback": current["host_fallback"], "query": query}, ensure_ascii=False), flush=True)
        return results
    finally:
        await session.stop()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await preparer.close()
        if intent:
            await intent.close()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    actions = [r for r in rows if r["action"]]
    chats = [r for r in rows if not r["action"]]
    return {"cases": len(rows), "action_cases": len(actions), "chat_cases": len(chats),
            "missed": sum(not r["provider_run_task"] for r in actions),
            "false_triggered": sum(r["provider_run_task"] for r in chats),
            "unsupported_completion_flags": sum(r["unsupported_completion_flag"] for r in rows),
            "host_fallback": sum(r["host_fallback"] for r in rows),
            "prepare_p50_ms": statistics.median(r["preparation"]["wall_ms"] for r in rows),
            "first_output_p50_ms": statistics.median(r["first_output_ms"] for r in rows),
            "response_created_p50_ms": statistics.median(r["response_created_ms"] for r in rows),
            "intent_ready": sum(r["preparation"]["jev"]["status"] == "ready" for r in rows)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("/tmp/boxagent-qwen-intent-ab"))
    parser.add_argument("--limit", type=int, default=len(CASES))
    parser.add_argument("--order", choices=("AB", "BA"), default="AB")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    summaries = {}
    for arm in args.order:
        rows = await run_arm(arm, args.out, args.limit)
        summaries[arm] = summarize(rows)
    a, b = summaries["A"], summaries["B"]
    report = {"model": load_settings().voice_model, "summaries": summaries,
              "checks": {"intent_budget": b["prepare_p50_ms"] <= 400,
                         "no_miss_regression": b["missed"] <= a["missed"],
                         "no_false_trigger_regression": b["false_triggered"] <= a["false_triggered"],
                         "no_completion_regression": b["unsupported_completion_flags"] <= a["unsupported_completion_flags"],
                         "text_latency": b["first_output_p50_ms"] <= a["first_output_p50_ms"]},
              "limits": "合成输入、每组连续会话、固定顺序且每例一次；编结果为词法筛查，须检查原文。不能证明真实用户总体改善或缓存命中。"}
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
