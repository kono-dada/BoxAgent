"""Verify cold and warm Codex native-history injection with a real provider."""

import argparse
import asyncio
import json
import os
from pathlib import Path

from boxagent.bootstrap.engine import create_application
from boxagent.bootstrap.settings import load_settings


class DisabledMemory:
    async def close(self):
        return None


async def add_qwen_exchange(application, user_text, assistant_text):
    context = await application.conversation_service.begin_interaction(
        user_text, source="text", runtime="qwen_realtime")
    await application.conversation_service.finish_interaction(
        context, status="succeeded", assistant_content=assistant_text,
        runtime="qwen_realtime")


async def run_task(application, goal, timeout):
    accepted = await application.start_task(goal, from_text=True)
    result = await asyncio.wait_for(
        asyncio.shield(application.job), timeout=timeout)
    return accepted, result


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def first_event(events, kind):
    return next(item for item in events if item.get("kind") == kind)


async def run(args):
    data_dir = args.data_dir.expanduser().resolve()
    os.environ["BOXAGENT_DATA_DIR"] = str(data_dir)
    os.environ["BOXAGENT_ENGINE_WATCH"] = "0"
    settings = load_settings()
    application = create_application(
        lambda _event: None,
        task_provider="deepseek", task_model=args.model,
        auto_approve=True, memory_backend=DisabledMemory(),
        app_settings=settings, context_interval=0)

    try:
        await application.start()
        await add_qwen_exchange(
            application, args.preface, "收到，我会保持简洁回答。")
        first, first_result = await run_task(
            application, args.first_goal, args.task_timeout)
        first_binding = await application.conversation_service.store.runtime_binding(
            first["session_id"], "codex")

        await add_qwen_exchange(
            application, args.marker, "收到你的临时代号。")
        second, second_result = await run_task(
            application, args.second_goal, args.task_timeout)
        second_binding = await application.conversation_service.store.runtime_binding(
            second["session_id"], "codex")
    finally:
        await application.close()

    first_events = read_events(
        settings.log_dir / "tasks" / first["task_id"] / "events.jsonl")
    second_events = read_events(
        settings.log_dir / "tasks" / second["task_id"] / "events.jsonl")
    first_injection = first_event(first_events, "runtime_history_injected")
    second_injection = first_event(second_events, "runtime_history_injected")
    second_reuse = first_event(second_events, "runtime_epoch_reused")

    summary = {
        "status": "succeeded",
        "provider": "deepseek",
        "model": args.model,
        "same_thread": first_binding.thread_id == second_binding.thread_id,
        "same_process": not any(
            item.get("kind") == "process_started" for item in second_events),
        "thread_id": second_binding.thread_id,
        "first_task": {
            "task_id": first["task_id"],
            "result_status": first_result.get("status"),
            "thread_state": first_injection["thread_state"],
            "injected_sequences": [
                first_injection["first_sequence"], first_injection["last_sequence"]],
            "injected_count": first_injection["message_count"],
        },
        "second_task": {
            "task_id": second["task_id"],
            "result_status": second_result.get("status"),
            "thread_state": second_injection["thread_state"],
            "injected_sequences": [
                second_injection["first_sequence"], second_injection["last_sequence"]],
            "injected_count": second_injection["message_count"],
        },
    }
    if not summary["same_thread"]:
        raise RuntimeError("两个任务没有复用同一 Codex Thread")
    if not summary["same_process"]:
        raise RuntimeError("第二个任务重新启动了 Codex App Server 进程")
    if first_injection["thread_state"] != "started":
        raise RuntimeError("首次任务没有在新 Thread 注入完整历史")
    if second_injection["thread_state"] != "reused":
        raise RuntimeError("第二次任务没有复用 Warm Thread")
    if second_injection["message_count"] != 2:
        raise RuntimeError("Warm Thread 没有只注入新增的 Qwen user/assistant 消息")

    output = data_dir / "native-history"
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--preface", default="我偏好简洁回答。")
    parser.add_argument("--marker", default="我的临时代号是海盐。")
    parser.add_argument("--first-goal", default=(
        "只读取 macOS 计算器当前显示，确认应用可访问，不要修改内容。"))
    parser.add_argument("--second-goal", default=(
        "只读取 macOS 计算器当前显示，不要修改内容；最终 summary 中带上我刚才说的临时代号。"))
    parser.add_argument("--task-timeout", type=float, default=300)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
