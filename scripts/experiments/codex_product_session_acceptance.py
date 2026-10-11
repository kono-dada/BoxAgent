"""Run two read-only synthetic tasks through the production Product Session."""

import asyncio
import json
import sys
import argparse
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from boxagent.application.memory import DisabledMemoryModule
from boxagent.bootstrap.engine import create_application
from boxagent.bootstrap.settings import load_settings


async def main(data: Path) -> None:
    base = load_settings()
    settings = replace(base, data_dir=data, log_dir=data / "logs",
                       jev_mem_cache=data / "memory/jev-mem", jev_mem_backend="mock",
                       task_provider="deepseek", task_model=base.deepseek_model)
    application = create_application(lambda _: None, app_settings=settings, voice_factory=lambda *_: None)
    application.memory = DisabledMemoryModule()
    application.interaction_service.memory = application.memory
    application.conversation_service.message_sinks = ()
    application.conversation_service.finalization_sinks = ()
    results = []
    try:
        await application.start()
        session = await application.conversation_service.active_session()
        async def chat(text):
            context = await application.conversation_service.begin_interaction(text, runtime="qwen_realtime")
            await application.conversation_service.finish_interaction(context, status="succeeded", assistant_content="收到。", runtime="qwen_realtime")
        await chat("我的临时代号是海盐；请简洁回答。")
        for index in range(2):
            if index:
                await chat("现在代号改成青柠，后面的回答按新代号。")
            accepted = await application.start_task(
                "这是协议验收，不执行任何工具或电脑操作。直接返回 outcome=blocked，summary 用一句话带上我的当前代号，并说明未执行任何操作，steps 为空。",
                from_text=True)
            result = await asyncio.wait_for(asyncio.shield(application.job), 100)
            binding = await application.conversation_service.store.runtime_binding(session.session_id, "codex")
            path = data / "sessions" / session.session_id / "tasks" / accepted["task_id"] / "events.jsonl"
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            request = next(e for e in rows if e["kind"] == "runtime_request_sent")
            results.append({"result": result, "thread_id": binding.thread_id,
                            "injected": request["injected_history"], "thread_state": request["thread_state"],
                            "reference_removed": any(e["kind"] == "runtime_reference_removed" for e in rows)})
        report = {"tasks": results, "checks": {"same_thread": results[0]["thread_id"] == results[1]["thread_id"],
                  "references_removed": all(r["reference_removed"] for r in results),
                  "current_fact": "青柠" in results[-1]["result"].get("summary", "")}}
        (data / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not all(report["checks"].values()):
            raise RuntimeError("Codex Product Session 验收失败")
    finally:
        await application.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    arguments = parser.parse_args()
    directory = arguments.out or Path(tempfile.mkdtemp(prefix="boxagent-codex-product-"))
    asyncio.run(main(directory))
