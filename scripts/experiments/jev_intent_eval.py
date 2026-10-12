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

from typesafe_sdk import Choice  # noqa: E402


def intent_questions():
    """“本轮是否要求操作电脑”的命题定义；只描述状态字段，不暴露问题 ID。

    v1（长指令 + 上一轮回复）在本地 Laya 上 AUC 0.45，接近随机；v2 改为单字段、
    两选项的 Choice（Laya AUC 0.83）；v3 改中文描述，阿里云 decision-model-preview
    上 AUC 0.99（校正标注后）。P(action) 取 ``operate`` 选项的概率。
    """
    return {
        "action": Choice(
            instructions="用户希望桌面助手做什么？",
            criteria={
                "operate": ("现在去操作电脑：打开/关闭应用或网页，播放、暂停、收藏、点赞、发消息、"
                            "搜索、计算、点击、输入、查看屏幕内容"),
                "chat": "聊天、提问、让助手记住或回忆某事、评论或抱怨，没有要求现在操作电脑",
            }),
    }


def load_client(backend):
    from boxagent.infrastructure.memory.jev_mem.core.jev_client import JevClient
    from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig

    # 不重试、不缓存：测的是单次调用的真实延迟。审计写到临时目录，不污染记忆库。
    options = {}
    api_key = None
    if backend == "aliyun":
        # 阿里云百炼托管同一 System One 协议；密钥用 DashScope Key。
        workspace = os.environ["BOXAGENT_DASHSCOPE_WORKSPACE_ID"]
        region = os.getenv("BOXAGENT_DASHSCOPE_REGION", "cn-beijing")
        options = {"jev_base_url": f"https://{workspace}.{region}.maas.aliyuncs.com/compatible-mode",
                   "jev_model": "decision-model-preview"}
        api_key = os.environ["DASHSCOPE_API_KEY"]
        backend = "jev"
    config = JevMemConfig(decision_backend=backend, timeout_seconds=3.0, max_retries=0,
                          cache_size=0, fallback_to_magma=False, **options)
    return JevClient(config, api_key=api_key, audit=None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--thresholds", default="0.5,0.8,0.9,0.95")
    parser.add_argument("--backend", default="aliyun", choices=("aliyun", "jev", "laya-mlx"))
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
            state = {"用户这句话": row["text"]}
            started = time.perf_counter()
            try:
                result = client.evaluate("intent", state, questions)
            except Exception as exc:  # 区域限制等硬失败：记录后继续
                print("调用失败：", exc, flush=True)
                result = None
            ms = (time.perf_counter() - started) * 1000
            p = (None if result is None
                 else result.choices["action"].probabilities.get("operate"))
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
