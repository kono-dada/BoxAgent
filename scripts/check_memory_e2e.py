#!/usr/bin/env python3
"""Validate Final User Message -> vendored Jev-Mem -> projections -> recall."""

import argparse
import asyncio
from dataclasses import replace
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from boxagent.bootstrap.engine import create_application
from boxagent.bootstrap.settings import load_settings
from boxagent.domain.conversation.models import ContextCheckpoint, RuntimeContextSegment


def require(condition, message):
    if not condition:
        raise AssertionError(message)


async def run(root, *, backend):
    legacy_snapshot = root / "memory/ledger/snapshot.json"
    legacy_snapshot.parent.mkdir(parents=True, exist_ok=True)
    legacy_snapshot.write_text(json.dumps({
        "schema_version": 1,
        "records": {
            "legacy-active": {
                "memory_id": "legacy-active", "revision": 1,
                "content": "迁移测试用户喜欢乌龙茶。", "status": "active",
                "kind": "preference", "source_mode": "automatic",
                "source_session_id": "legacy-session",
                "source_interaction_id": "legacy-interaction",
                "source_event_ids": ["legacy-event"],
                "backend_links": [], "created_at": 1, "updated_at": 2,
            },
            "legacy-deleted": {
                "memory_id": "legacy-deleted", "revision": 2,
                "content": "这条已删除的旧记忆不能复活。", "status": "deleted",
                "backend_links": [],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    base = load_settings()
    settings = replace(
        base, data_dir=root, log_dir=root / "logs",
        codex_home=root / "codex-home",
        jev_mem_cache=root / "memory/jev-mem",
        task_provider="deepseek", task_model="deepseek-flash",
        deepseek_api_key="" if backend == "mock" else base.deepseek_api_key,
        jev_mem_backend=backend)
    application = create_application(
        lambda _event: None, app_settings=settings, context_interval=0)
    report = {"data_dir": str(root), "backend": backend, "checks": {}}
    try:
        await application.start()
        await application.memory.await_idle()
        migrated = await application.memory.list_memories()
        require(any("乌龙茶" in item["content"] for item in migrated),
                "旧 Canonical active 记录未导入 Jev-Mem")
        require(not any("不能复活" in item["content"] for item in migrated),
                "旧 deleted 记录被错误导入 Jev-Mem")
        migration = (await application.memory.health())["legacy_migration"]
        require(migration.get("status") == "completed",
                "旧 Canonical 导入未完成")
        report["checks"]["legacy_canonical_upgrade"] = "passed"
        conversation = application.conversation_service

        first = await conversation.begin_interaction(
            "我偏好简洁回答，以后叫我小明。",
            source="text", runtime="qwen_realtime")
        await application.memory.await_idle()
        jobs_before_reply = (await application.memory.health())["ingestion"]["job_statuses"]
        require(jobs_before_reply.get("completed") == 1,
                "Final User Message 落盘后未完成 Jev-Mem admission")
        await conversation.finish_interaction(
            first, status="succeeded", assistant_content="好的，小明。",
            runtime="qwen_realtime")
        records = await application.memory.list_memories()
        require(any("小明" in item["content"] for item in records),
                "Jev-Mem 未保存原始用户消息")
        report["checks"]["user_commit_to_jev"] = "passed"

        second = await application.create_session("跨会话验证")
        recalled = await application.recall_memory("用户希望我怎么称呼他？")
        profile = await application.memory.stable_profile()
        packet = await application.memory.context_packet(
            "用户希望我怎么称呼他？", session_id=second["session_id"])
        require("小明" in str(recalled), "跨 Session Jev-Mem recall 未命中")
        require("小明" in profile, "User Profile 未包含称呼偏好")
        require("小明" in packet, "L2 context 未注入相关记忆")
        report["checks"]["cross_session_l2_recall"] = "passed"

        checkpoint = ContextCheckpoint(
            checkpoint_id="ckp_e2e", session_id=first.session.session_id,
            runtime="qwen_realtime", source_from_sequence=1,
            covered_through_sequence=3, source_hash="e2e", created_at=time.time(),
            provider="deepseek", model="deepseek-flash",
            content={
                "summary": "用户正在验证 BoxAgent 的 Jev-Mem-first 长期记忆链路。",
                "user_facts": ["用户偏好简洁回答"], "decisions": [],
                "outcomes": ["原始用户消息已进入 Jev-Mem"], "open_loops": [],
                "entities": ["BoxAgent", "Jev-Mem"], "commitments": [],
                "time_range": {"start": None, "end": None},
                "salient_events": [{"event_id": first.user_event.event_id,
                                     "description": "用户给出称呼和回答偏好"}],
            })
        segment = RuntimeContextSegment(
            segment_id="seg_e2e", session_id=first.session.session_id,
            runtime="qwen_realtime", start_sequence=1, end_sequence=3,
            checkpoint_id=checkpoint.checkpoint_id, created_at=time.time())
        await application.memory.checkpoint_created(checkpoint, segment)
        narrative = await application.recall_memory("长期记忆链路验证进展")
        require(any(item.get("type") == "NARRATIVE"
                    for item in narrative["memories"]),
                "Checkpoint Narrative 未被 Jev-Mem 召回")
        report["checks"]["narrative_recall"] = "passed"

        try:
            await application.remember_memory(
                "API_KEY=" + "sk-" + "example-only-1234567890")
        except ValueError as exc:
            require("不能写入长期记忆" in str(exc), "Secret 拒绝原因错误")
        else:
            raise AssertionError("敏感凭据不应写入 Jev-Mem")
        report["checks"]["secret_rejection"] = "passed"

        target = next(item for item in records if "小明" in item["content"])
        deleted = await application.delete_memory_node(target["id"])
        require(deleted["status"] == "succeeded", "精确删除失败")
        after_delete = await application.recall_memory("用户希望我怎么称呼他？")
        require(target["id"] not in {
            item["id"] for item in after_delete["memories"]},
            "删除的 Jev-Mem 节点仍被召回")
        report["checks"]["delete_no_resurrection"] = "passed"

        report["health"] = await application.memory.health()
        report["status"] = "passed"
        return report
    finally:
        await application.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--backend", choices=("auto", "jev", "mock"),
                        default="auto")
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
        print(json.dumps(asyncio.run(run(root, backend=arguments.backend)),
                         ensure_ascii=False, indent=2))
    finally:
        if temporary and not arguments.keep:
            shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
