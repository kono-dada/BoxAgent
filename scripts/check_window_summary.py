"""单次真实前台窗口推理检查，不启动语音或执行器。"""

import asyncio
import json

from boxagent.context import WindowSummary


async def main():
    done = asyncio.Event()
    result = {}

    def emit(kind, **fields):
        print(json.dumps({"type": kind, **fields}, ensure_ascii=False), flush=True)
        if kind in {"context.updated", "context.error"}:
            result.update(type=kind)
            done.set()

    observer = WindowSummary(emit, interval=3)
    observer.task = asyncio.create_task(observer.run())
    try:
        await asyncio.wait_for(done.wait(), 180)
    finally:
        await observer.stop()
    if result.get("type") != "context.updated":
        raise SystemExit("真实窗口摘要检查失败，请查看 context-worker.log")
