"""Real Qwen -> Codex task -> Qwen notification smoke test with isolated data."""

import argparse
import asyncio
from dataclasses import asdict
import json
import os
import time
from pathlib import Path

from boxagent.bootstrap.engine import create_application
from boxagent.bootstrap.settings import load_settings
from boxagent.core.errors import redact
from boxagent.agent.harness import RuntimeRequestBuilder
from boxagent.agent.harness.request import HarnessInput
from boxagent.agent.harness.persona import load_persona


class DisabledMemory:
    async def close(self):
        return None


async def wait_for_value(factory, *, timeout, interval=0.1, message):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if value := factory():
            return value
        await asyncio.sleep(interval)
    raise TimeoutError(message)


async def wait_for_interaction(application, interaction_id, *, timeout):
    async def terminal_event():
        session = await application.conversation_service.active_session()
        if session is None:
            return None
        events = await application.session_events(session.session_id)
        return next((item for item in reversed(events)
                     if item.get("type") == "interaction.finalized"
                     and item.get("interaction_id") == interaction_id), None)

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if terminal := await terminal_event():
            return terminal
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Interaction {interaction_id} 未在限时内完成")


async def run(args):
    data_dir = args.data_dir.expanduser().resolve()
    os.environ["BOXAGENT_DATA_DIR"] = str(data_dir)
    os.environ["BOXAGENT_ENGINE_WATCH"] = "0"
    settings = load_settings()
    output = data_dir / "front-task-notification"
    output.mkdir(parents=True, exist_ok=True)
    events = []

    def publish(event):
        events.append(redact(event))

    application = create_application(
        publish, task_provider="deepseek", task_model=args.model,
        auto_approve=True, memory_backend=DisabledMemory(),
        app_settings=settings, context_interval=0)
    started_at = time.time()
    try:
        await application.start()
        preface = await application.submit_text(args.preface)
        preface_terminal = await wait_for_interaction(
            application, preface["interaction_id"], timeout=args.turn_timeout)

        first = await application.submit_text(args.goal)
        job = await wait_for_value(
            lambda: application.job, timeout=args.route_timeout,
            message="Qwen 未在限时内委托后台任务")
        routed_at = time.time()

        task_interaction = application.execution_service.interaction
        delegated_goal = application.execution_service.last_result.get("goal", args.goal)
        prior_messages = tuple(task_interaction.prior_messages)
        runtime_binding = task_interaction.runtime_binding
        compiled_request = RuntimeRequestBuilder(
            persona=load_persona(settings.soul_file)).prepare(HarnessInput(
                goal=delegated_goal,
                turns=prior_messages,
                memories=(),
                context_cursor=(runtime_binding.context_cursor
                                if runtime_binding else 0)))
        qwen_restore_context = getattr(application.voice, "conversation_context", "")

        second = await application.submit_text(args.chat)
        chat_terminal = await wait_for_interaction(
            application, second["interaction_id"], timeout=args.turn_timeout)
        result = await asyncio.wait_for(asyncio.shield(job), args.task_timeout)
        completed_at = time.time()

        async def delivered_notification():
            items = await application.list_notifications(pending_only=False)
            return next((item for item in items
                         if item.get("status") == "delivered"), None)

        deadline = asyncio.get_running_loop().time() + args.notification_timeout
        delivered = None
        while asyncio.get_running_loop().time() < deadline:
            delivered = await delivered_notification()
            if delivered:
                break
            await asyncio.sleep(0.2)

        session = await application.conversation_service.active_session()
        session_events = (await application.session_events(session.session_id)
                          if session else [])
        notifications = await application.list_notifications(pending_only=False)
        summary = {
            "status": "succeeded" if result.get("status") == "succeeded"
                      and delivered else "partial",
            "provider": "deepseek",
            "model": args.model,
            "preface_submission": preface,
            "preface_terminal": preface_terminal,
            "task_submission": first,
            "chat_submission": second,
            "chat_terminal": chat_terminal,
            "task_result": result,
            "notification": delivered,
            "notifications": notifications,
            "session_id": session.session_id if session else None,
            "conversation_event_types": [item.get("type") for item in session_events],
            "final_messages": [
                {"sequence": item.get("sequence"),
                 "interaction_id": item.get("interaction_id"),
                 "runtime": item.get("runtime"),
                 "role": item.get("role"), "content": item.get("content")}
                for item in session_events if item.get("type") == "message.final"
            ],
            "qwen_context": {
                "connection_restore_context": qwen_restore_context,
                "history_mode": "same_realtime_connection",
            },
            "codex_context": {
                "query": compiled_request.query,
                "history": [asdict(item) for item in compiled_request.history],
                "history_delta": [
                    asdict(item) for item in compiled_request.history_delta],
                "evidence_context": compiled_request.evidence_context,
                "effective_turn_input": compiled_request.query,
                "developer_instructions": compiled_request.developer_instructions,
                "developer_instructions_characters": len(
                    compiled_request.developer_instructions),
                "runtime_binding_before_task": (
                    runtime_binding.payload() if runtime_binding else None),
            },
            "route_seconds": round(routed_at - started_at, 3),
            "task_seconds": round(completed_at - routed_at, 3),
            "elapsed_seconds": round(time.time() - started_at, 3),
            "event_types": [item.get("type") for item in events],
        }
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        if not delivered:
            raise RuntimeError("任务已结束，但未在限时内收到播放回执")
    finally:
        await application.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--preface", default=(
        "我最近在做 BoxAgent，并且偏好简洁回答。"))
    parser.add_argument("--goal", default=(
        "打开 macOS 计算器，计算 21+21，并确认最终显示为 42。"
        "只操作计算器，不要修改其他内容。"))
    parser.add_argument("--chat", default=(
        "趁后台执行时，请讲一句很短的冷笑话，不要查询或取消任务。"))
    parser.add_argument("--route-timeout", type=float, default=45)
    parser.add_argument("--task-timeout", type=float, default=300)
    parser.add_argument("--notification-timeout", type=float, default=60)
    parser.add_argument("--turn-timeout", type=float, default=60)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
