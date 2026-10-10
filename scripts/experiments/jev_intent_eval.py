"""离线评估 JEV 意图识别问题定义（REQ-02）。

在 Jev-Mem 的独立解释器中运行，读取标注集，对每条用户原文调用一次
JEV Decision Model，输出每条的 P(action)、延迟，以及按阈值统计的
漏判率与误触发率。

用法：
    .runtime/jev-mem-venv/bin/python scripts/experiments/jev_intent_eval.py \
        --labels /tmp/req02/labels.jsonl --out /tmp/req02/result.jsonl

标注集每行：{"text": "...", "action": true|false, "context": "上一轮助手回复（可空）"}
会把每条原文发送到 TypeSafe.ai；结果文件含用户原文，不要提交到仓库。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from typesafe_sdk import Noul  # noqa: E402


def intent_questions():
    """“本轮是否要求操作电脑”的命题定义；只描述状态字段，不暴露问题 ID。"""
    return {
        "action": Noul(
            instructions=(
                "Does `user_utterance` ask the assistant to operate the user's computer "
                "now, i.e. open, close, click, type into, play, pause, favourite, search in "
                "or read the screen of an application, website or system setting? "
                "Use `previous_assistant_reply` only to resolve short follow-ups such as "
                "\"继续\" or \"安装\"."),
            criteria={
                "true": ("The utterance requests, confirms or continues a concrete operation "
                         "on the computer that the assistant should carry out in this turn, "
                         "including a reply that confirms a computer operation the assistant "
                         "has just offered."),
                "false": ("Chit-chat, a question about the assistant or the user, a request "
                          "to remember or recall something, a complaint that a previous "
                          "operation failed without asking to retry, or an unfinished "
                          "sentence with no request."),
            }),
    }


def load_client(backend):
    from boxagent.infrastructure.memory.jev_mem.core.jev_client import JevClient
    from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig

    # 不重试、不缓存：测的是单次调用的真实延迟。审计写到临时目录，不污染记忆库。
    config = JevMemConfig(decision_backend=backend, timeout_seconds=3.0, max_retries=0,
                          cache_size=0, fallback_to_magma=False)
    return JevClient(config, audit=None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--thresholds", default="0.5,0.8,0.9,0.95")
    parser.add_argument("--backend", default="jev", choices=("jev", "laya-mlx"))
    args = parser.parse_args()
    if args.backend == "jev" and not os.getenv("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY 未设置")
    rows = [json.loads(line) for line in args.labels.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    client = load_client(args.backend)
    questions = intent_questions()
    results = []
    with args.out.open("w", encoding="utf-8") as out:
        for row in rows:
            state = {"user_utterance": row["text"],
                     "previous_assistant_reply": row.get("context", "")}
            started = time.perf_counter()
            result = client.probabilities("intent", state, questions)
            ms = (time.perf_counter() - started) * 1000
            p = None if result is None else result.values.get("action")
            record = {**row, "p_action": p, "ms": round(ms)}
            results.append(record)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"{'✓' if row['action'] else '·'} p={p if p is None else round(p, 2)!s:>5} "
                  f"{ms:5.0f}ms  {row['text'][:40]}", flush=True)
    scored = [r for r in results if r["p_action"] is not None]
    ms = sorted(r["ms"] for r in scored)
    print(f"\n成功 {len(scored)}/{len(results)}；延迟 p50 {ms[len(ms) // 2]} ms，"
          f"p90 {ms[int(len(ms) * 0.9)]} ms")
    positives = sum(r["action"] for r in scored)
    negatives = len(scored) - positives
    for threshold in (float(x) for x in args.thresholds.split(",")):
        missed = sum(r["action"] and r["p_action"] < threshold for r in scored)
        false_hits = sum((not r["action"]) and r["p_action"] >= threshold for r in scored)
        print(f"阈值 {threshold:.2f}：漏判 {missed}/{positives}，误触发 {false_hits}/{negatives}")


if __name__ == "__main__":
    main()
