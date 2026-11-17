"""Opt-in live Search/Fetch acceptance using only a public game question."""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from boxagent.bootstrap.settings import load_settings
from boxagent.bootstrap.web import create_web_research


def file_setting(path: Path, name: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


async def run(query: str, *, env_file: Path | None, output: Path) -> bool:
    settings = load_settings()
    if env_file is not None:
        settings = replace(settings,
            deepseek_api_key=file_setting(env_file, "DEEPSEEK_API_KEY"),
            deepseek_base_url=file_setting(env_file, "BOXAGENT_DEEPSEEK_BASE_URL") or "https://api.deepseek.com")
    if not settings.deepseek_api_key:
        raise ValueError("请配置 DEEPSEEK_API_KEY；脚本不会使用模拟结果")
    service = create_web_research(replace(settings, web_search_enabled=True))
    started = time.monotonic()
    search = await service.search(query, count=3, scope="live-smoke")
    sources = search.get("sources", [])
    read = None
    attempts = []
    for source in sources:
        read = await service.read(source["url"], scope="live-smoke")
        attempts.append({"url": source["url"], "status": read["status"], "code": read.get("code", "")})
        if read["status"] == "succeeded":
            break
    elapsed = round(time.monotonic() - started, 3)
    output.mkdir(parents=True, exist_ok=True)
    (output / "evidence.json").write_text(json.dumps({
        "search": search, "read": read, "read_attempts": attempts, "elapsed_seconds": elapsed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "search_status": search["status"], "message": search["message"],
        "sources": [{"title": item["title"], "url": item["url"], "published_at": item["published_at"]} for item in sources],
        "read_status": read["status"] if read else "not_requested",
        "read_attempts": attempts,
        "read_characters": len(read.get("content", "")) if read else 0,
        "excerpt": read.get("content", "")[:500] if read else "",
        "elapsed_seconds": elapsed, "evidence": str(output.resolve() / "evidence.json"),
    }, ensure_ascii=False, indent=2))
    return search["status"] == "succeeded" and read is not None and read["status"] == "succeeded"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="明确执行按次计费的真实搜索与网页读取")
    parser.add_argument("--query", default="鸣潮 无冠者 新手打法 攻略")
    parser.add_argument("--env-file", type=Path, help="可选：只读取指定文件中的 DeepSeek Key 和 API 地址")
    parser.add_argument("--output", type=Path, default=Path(".runtime/web-research"))
    args = parser.parse_args()
    if not args.live:
        parser.error("使用 --live 才会执行最多四次真实 API 调用；不会发送聊天历史或截图")
    succeeded = asyncio.run(run(args.query, env_file=args.env_file, output=args.output))
    raise SystemExit(0 if succeeded else 1)


if __name__ == "__main__":
    main()
