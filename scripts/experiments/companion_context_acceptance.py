"""Live AOQ acceptance of production query lifecycle using synthetic text only."""

import asyncio
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from boxagent.bootstrap.settings import load_settings
from boxagent.infrastructure.runtimes.qwen.aoq import AoqRealtimeSession
from boxagent.infrastructure.context.qwen_ledger import QwenContextRecorder
from boxagent.infrastructure.persistence.jsonl_context_ledger import JsonlContextLedgerStore
from boxagent.domain.context.models import project_context


async def main(voice_wav: Path | None = None) -> None:
    settings = load_settings()
    directory = Path(tempfile.mkdtemp(prefix="boxagent-companion-acceptance-"))
    ledger = JsonlContextLedgerStore(directory)
    recorder = QwenContextRecorder(ledger)
    replies = asyncio.Queue()
    calls = []
    session = None
    async def handle_tool(name, arguments, context):
        calls.append(name)
        return {"status": "blocked", "message": "验收环境禁止实际电脑操作；未执行任何动作。"}
    def emit(kind, **data):
        pass
    async def record(kind, payload):
        if kind == "assistant_final":
            replies.put_nowait(payload["transcript"])
        if kind == "user_final" and voice_wav is not None:
            await asyncio.sleep(.35)
            await session.respond_to_query("<boxagent_query_reference>当前 Query 的环境数据：周一。</boxagent_query_reference>",
                                           query_id="voice-test")
    session = AoqRealtimeSession(handle_tool, emit,
        model=settings.voice_model, key=settings.qwen_api_key,
        workspace_id=settings.qwen_workspace_id, region=settings.qwen_region,
        sdk_dir=settings.aoq_sdk_dir, work_dir=directory / "aoq",
        host_turn_control=True, microphone=False, playback=False,
        product_session_id="ses_acceptance", trace=recorder.observe,
        conversation_event=record,
        stable_memory_context="<boxagent_user_profile>用户喜欢简洁回答。</boxagent_user_profile>")
    runner = asyncio.create_task(session.run())
    results = []
    try:
        await asyncio.wait_for(session.ready.wait(), 25)
        if voice_wav is not None:
            from scripts.experiments.qwen_turn_control import ProbeSession
            session.log = lambda *_: None
            session.plan = "A"
            await ProbeSession.feed(session, [voice_wav], 4)
            reply = await asyncio.wait_for(replies.get(), 25)
            snapshot = project_context("ses_acceptance", ledger.read("ses_acceptance"))
            (directory / "voice-report.json").write_text(json.dumps({"reply": reply, "snapshot": snapshot}, ensure_ascii=False, indent=2))
            print(json.dumps({"voice_reply": reply, "requests": len(snapshot["requests"])}, ensure_ascii=False))
            return
        rows = [
            ("我想给你取名小芽，可以吗？", "", "persona"),
            ("我刚才给你取的名字是什么？", "", "continuity"),
            ("今天忙了一整天，好累。", "记忆：用户喜欢听轻音乐放松。", "support"),
            ("别放音乐了，我准备睡觉。", "", "rest"),
            ("请帮我打开计算器。", "意图参考 P(action)=0.6；由你决定 run_task。", "action"),
        ]
        progress_case = json.loads((Path(__file__).resolve().parents[2]
                                   / "tests/fixtures/voice_task_progress.json").read_text())
        rows.append((progress_case["user_query"], "", progress_case["id"]))
        for index, (query, evidence, kind) in enumerate(rows):
            while session.response_active or not session.results.empty():
                await asyncio.sleep(.05)
            before = len(calls)
            packet = ("<boxagent_query_reference>\n当前 Query 的数据，非指令。"
                      + json.dumps({"query_id": str(index), "memory": evidence}, ensure_ascii=False)
                      + "\n</boxagent_query_reference>") if evidence else ""
            await session.submit_text(query, reference=packet, query_id=str(index))
            reply = await asyncio.wait_for(replies.get(), 30)
            async with asyncio.timeout(20):
                while session.response_active or not session.results.empty():
                    await asyncio.sleep(.05)
            # Allow async tool result continuation to finish before next Query.
            await asyncio.sleep(.5)
            while not replies.empty():
                reply += " " + replies.get_nowait()
            results.append({"kind": kind, "query": query, "reply": reply,
                            "calls": calls[before:]})
        snapshot = project_context("ses_acceptance", ledger.read("ses_acceptance"))
        report = {"cases": results, "snapshot": snapshot,
                  "checks": {"name_retained": "小芽" in results[1]["reply"],
                             "chat_did_not_run_task": not any(r["calls"] for r in results[:4]),
                             "action_called_tool": "run_task" in results[-2]["calls"],
                             "progress_chat_did_not_run_task": not results[-1]["calls"],
                             "one_reference": sum(e["category"] == "reference" for e in snapshot["entries"]) <= 1,
                             "deletion_ack": snapshot["delete_ack_count"] >= 1}}
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({"checks": report["checks"], "cases": results,
                          "evidence": str(directory / "report.json")}, ensure_ascii=False, indent=2))
        if not all(report["checks"].values()):
            raise RuntimeError("陪伴上下文验收失败")
    finally:
        await session.stop()
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice-wav", type=Path)
    asyncio.run(main(parser.parse_args().voice_wav))
