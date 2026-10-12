"""FIX-01 离线验收：默认生成 12 张合成界面，也可 --images 读取本机截图。

    .venv/bin/python scripts/experiments/window_summary_eval.py
    .venv/bin/python scripts/experiments/window_summary_eval.py --images PATH --out PATH

不调用云端 API；原始截图、摘要和结果写入忽略目录。生产配置与 worker 共用。
"""

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from boxagent.infrastructure.perception.qwen_mlx import clean_summary  # noqa: E402
from scripts.window_summary_worker import (  # noqa: E402
    GENERATION_OPTIONS, SUMMARY_PROMPT, summarize_image,
)

CONFIGS = {
    "baseline": {"max_tokens": 512, "temperature": 0.0},
    "rp1.15_512": {**GENERATION_OPTIONS, "max_tokens": 512},
    "production": dict(GENERATION_OPTIONS),
}


def synthetic_images(directory):
    """仓库代码生成固定输入，无需共享私人截图或另行下载样本。"""
    from PIL import Image, ImageDraw, ImageFont

    directory.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=24)
    pages = [
        ("Code editor", ["BoxAgent / perception.py", "def observe_window():",
                         "    capture_screen()", "    summarize_image()", "Tests: passed"]),
        ("Conversation", ["User: Open the calculator", "Assistant: Starting the task",
                           "Task status: completed", "User: Thanks", "Assistant: You're welcome"]),
        ("Memory dashboard", ["Sessions: 12", "Memory nodes: 37", "Recall latency: 126 ms",
                               "Last query: preferred response style", "Status: ready"]),
        ("Experiment results", ["Model: Qwen 0.8B", "Samples: 12", "Repetition penalty: 1.15",
                                 "Token budget: 256", "Evaluation: local inference"]),
        ("Requirements", ["REQ-01: Context ledger complete", "INFRA-03: Logging complete",
                           "FIX-01: Window summary", "INFRA-01: Design review", "Next: Session storage"]),
        ("Runtime monitor", ["Engine: running", "Voice: idle", "Observation: enabled",
                              "Capture failures: 0", "Active window: Code editor"]),
    ]
    paths = []
    for index in range(12):
        title, lines = pages[index % len(pages)]
        image = Image.new("RGB", (960, 640), "#f3f5f8")
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 960, 68), fill="#223047")
        draw.text((24, 20), f"{title} - view {index + 1}", font=font, fill="white")
        draw.rectangle((16, 92, 210, 610), fill="#e0e5ed")
        draw.text((30, 112), "Workspace", font=font, fill="#223047")
        for row, text in enumerate(lines):
            draw.text((236, 116 + row * 72), text, font=font, fill="#223047")
        path = directory / f"{index:02}.png"
        image.save(path)
        paths.append(path)
    return paths


def metrics(text):
    sentences = [s.strip() for s in re.split(r"[。！？!?\n]", text) if len(s.strip()) > 4]
    counts = Counter(text[i:i + 12] for i in range(max(0, len(text) - 11)))
    return {"repeated": len(sentences) != len(set(sentences)),
            "ngram_rep": sum(n - 1 for n in counts.values()) / max(1, sum(counts.values()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, help="本机 PNG/JPG 目录；省略时生成合成界面")
    parser.add_argument("--model", type=Path, default=ROOT / "models/qwen3.5-0.8b-mlx")
    parser.add_argument("--out", type=Path, default=ROOT / ".runtime/window-summary-eval")
    parser.add_argument("--configs", nargs="+", choices=list(CONFIGS),
                        default=["baseline", "production"])
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    images = (sorted(p for p in args.images.iterdir()
                     if p.suffix.lower() in {".png", ".jpg", ".jpeg"})
              if args.images else synthetic_images(args.out / "synthetic"))
    if not images:
        parser.error("没有可评估的图片")
    with contextlib.redirect_stdout(sys.stderr):
        from mlx_vlm import load
        model, processor = load(str(args.model))
    config = model.config if isinstance(model.config, dict) else vars(model.config)
    report = {
        "model": str(args.model.resolve()),
        "versions": {name: importlib.metadata.version(name) for name in ("mlx", "mlx-vlm", "pillow")},
        "prompt": SUMMARY_PROMPT,
        "fixtures": [{"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                     for p in images],
        "results": {}, "summary": {},
    }
    for name in args.configs:
        rows = []
        for path in images:
            started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                result = summarize_image(model, processor, config, path, options=CONFIGS[name])
            text = result.text.strip()
            cleaned = clean_summary(text)
            rows.append({"file": path.name, "sec": time.perf_counter() - started,
                         "chars": len(text), "tokens": result.generation_tokens,
                         "text": text, "cleaned": cleaned, **metrics(text),
                         "clean_repeated": metrics(cleaned)["repeated"]})
        report["results"][name] = {"options": CONFIGS[name], "rows": rows}
        summary = {"samples": len(rows), "sec_p50": round(statistics.median(r["sec"] for r in rows), 3),
                   "chars_p50": statistics.median(r["chars"] for r in rows),
                   "chars_max": max(r["chars"] for r in rows),
                   "tokens_max": max(r["tokens"] for r in rows),
                   "repeated_samples": sum(r["repeated"] for r in rows),
                   "ngram_rep_mean": round(statistics.mean(r["ngram_rep"] for r in rows), 4),
                   "clean_repeated_samples": sum(r["clean_repeated"] for r in rows),
                   "empty_samples": sum(not r["cleaned"] for r in rows)}
        report["summary"][name] = summary
        print(name, json.dumps(summary, ensure_ascii=False), flush=True)
        (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    production = report["summary"].get("production")
    if production and any(production[k] for k in
                          ("repeated_samples", "clean_repeated_samples", "empty_samples")):
        raise SystemExit("生产配置验收失败：有复读或空摘要；见 report.json")


if __name__ == "__main__":
    main()
