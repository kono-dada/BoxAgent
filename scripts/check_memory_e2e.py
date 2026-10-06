#!/usr/bin/env python3
"""Run the real DeepSeek -> Canonical Ledger -> Jev memory acceptance flow."""

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxagent.bootstrap.engine import create_application
from boxagent.bootstrap.settings import load_settings
from boxagent.domain.memory.models import MemoryCandidate


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def run(root):
    base = load_settings()
    settings = replace(
        base, data_dir=root, log_dir=root / "logs",
        codex_home=root / "codex-home",
        jev_memory_cache=root / "memory/jev",
        task_provider="deepseek", task_model="deepseek-flash",
        jev_memory_backend="jev")
    application = create_application(
        lambda _event: None, app_settings=settings, context_interval=0)
    extraction = next(
        item for item in application.runtime_resources
        if item.__class__.__name__ == "MemoryExtractionCoordinator")
    report = {"data_dir": str(root), "checks": {}}
    try:
        await application.start()

        first = await application.conversation_service.begin_interaction(
            "我平时最喜欢爵士乐，而且回答请尽量简洁。",
            source="text", runtime="qwen_realtime")
        await application.conversation_service.finish_interaction(
            first, status="succeeded",
            assistant_content="好的，我会保持简洁。", runtime="qwen_realtime")
        await extraction.drain()
        records = await application.memory_service.ledger.memories()
        active = [item for item in records if item.status == "active"]
        require(any("爵士" in item.content for item in active),
                "DeepSeek 未提取音乐偏好")
        require(any("简洁" in item.content for item in active),
                "DeepSeek 未提取回答偏好")
        report["checks"]["automatic_extraction"] = "passed"

        second_session = await application.create_session("跨会话验证")
        recalled = await application.recall_memory(
            "用户最喜欢什么音乐，回答风格是什么？")
        profile = await (
            application.interaction_service.memory_context_provider.stable_profile())
        require("爵士" in str(recalled) and "简洁" in str(recalled),
                "跨 Session 召回未返回两项偏好")
        require("爵士" in profile and "简洁" in profile,
                "Stable Profile 未包含已确认偏好")
        report["checks"]["cross_session_recall"] = "passed"
        report["second_session_id"] = second_session["session_id"]

        correction = await application.conversation_service.begin_interaction(
            "更正一下：我现在最喜欢古典音乐，不再是爵士乐。",
            source="text", runtime="qwen_realtime")
        await application.conversation_service.finish_interaction(
            correction, status="succeeded",
            assistant_content="明白，已经按新偏好理解。", runtime="qwen_realtime")
        await extraction.drain()
        records = await application.memory_service.ledger.memories()
        active_music = [item for item in records
                        if item.status == "active"
                        and item.canonical_slot == "preference:music_genre"]
        require(any("古典" in item.content for item in active_music),
                "偏好更正没有生成新的 active 记忆")
        require(any(item.status == "superseded" and "爵士" in item.content
                    for item in records), "旧偏好未保留为 superseded revision")
        report["checks"]["semantic_correction"] = "passed"

        pending_candidate = MemoryCandidate(
            content="用户希望晚上减少咖啡因摄入。", subject="user",
            kind="preference", durability="stable", operation="add",
            confidence=.95, sensitivity="sensitive",
            canonical_slot="preference:evening_caffeine",
            evidence=({"event_id": correction.user_event.event_id,
                       "quote": "晚上减少咖啡因"},))
        pending = (await application.memory_service.admit_candidate(
            pending_candidate, source_mode="automatic",
            session_id=correction.session.session_id,
            interaction_id=correction.interaction_id,
            source_event_ids=(correction.user_event.event_id,)))["memory"]
        require(pending.status == "pending_review", "敏感候选未进入待确认")
        approved = await application.approve_memory(pending.memory_id)
        require(approved["memory"]["status"] == "active", "待确认记忆批准失败")
        report["checks"]["pending_review_approval"] = "passed"

        try:
            rejected = await application.remember_memory(
                "请记住我的 API_KEY=" + "sk-" + "example-only-1234567890")
        except ValueError as exc:
            require("不能写入长期记忆" in str(exc), "敏感信息拒绝原因不正确")
        else:
            raise AssertionError(f"敏感凭据不应写入：{rejected}")
        report["checks"]["secret_rejection"] = "passed"

        records = await application.memory_service.ledger.memories()
        concise = next(item for item in records
                       if item.status == "active" and "简洁" in item.content)
        await application.delete_memory_node(concise.memory_id)
        profile = await (
            application.interaction_service.memory_context_provider.stable_profile())
        recalled = await application.recall_memory("回答要简洁吗？")
        raw = await application.memory_service.backend.query("简洁", top_k=10)
        require("简洁" not in profile, "删除后 Stable Profile 仍包含旧记忆")
        require("简洁" not in str(recalled), "删除后 Canonical recall 仍返回旧记忆")
        require("简洁" not in str(raw.get("memories", [])),
                "删除后 Jev 仍返回旧记忆")
        report["checks"]["safe_delete"] = "passed"

        snapshot = await application.memory_snapshot(node_limit=100, edge_limit=200)
        require(snapshot["statistics"]["status_counts"].get("deleted", 0) >= 1,
                "记忆看板快照没有 deleted 状态")
        require(any(node.get("revision", 0) >= 2 for node in snapshot["nodes"]),
                "记忆看板快照没有 revision")
        report["checks"]["dashboard_projection"] = "passed"

        jobs = await application.memory_service.ledger.jobs()
        report["job_statuses"] = [item.status for item in jobs]
        report["memory_statuses"] = {
            status: sum(item.status == status for item in
                        await application.memory_service.ledger.memories())
            for status in {item.status for item in
                           await application.memory_service.ledger.memories()}
        }
        report["status"] = "passed"
        return report
    finally:
        await application.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--keep", action="store_true")
    arguments = parser.parse_args()
    temporary = None
    if arguments.data_dir:
        root = arguments.data_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.mkdtemp(prefix="boxagent-memory-e2e-")
        root = Path(temporary)
    try:
        print(json.dumps(asyncio.run(run(root)), ensure_ascii=False, indent=2))
    finally:
        if temporary and not arguments.keep:
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
