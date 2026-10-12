"""Probe native additionalContext across two turns without tools or file writes."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from boxagent.infrastructure.runtimes.codex.app_server import resolve_codex_runtime


async def main() -> None:
    process = await asyncio.create_subprocess_exec(str(resolve_codex_runtime()), "app-server", "--stdio",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    pending = {}
    finished = asyncio.Queue()
    observed = []
    async def receive():
        while line := await process.stdout.readline():
            event = json.loads(line)
            if "id" in event and event["id"] in pending:
                pending.pop(event["id"]).set_result(event)
            if event.get("method") == "turn/completed":
                finished.put_nowait(event)
            if event.get("method") in {"item/completed", "turn/completed"}:
                observed.append(event)
    reader = asyncio.create_task(receive())
    seq = 0
    async def rpc(method, params):
        nonlocal seq
        seq += 1
        future = asyncio.get_running_loop().create_future()
        pending[seq] = future
        process.stdin.write((json.dumps({"id": seq, "method": method, "params": params}) + "\n").encode())
        await process.stdin.drain()
        event = await asyncio.wait_for(future, 30)
        if "error" in event:
            raise RuntimeError(event["error"])
        return event["result"]
    try:
        await rpc("initialize", {"clientInfo": {"name": "boxagent-context-probe", "version": "1"},
                                  "capabilities": {"experimentalApi": True}})
        process.stdin.write(b'{"method":"initialized"}\n')
        thread = await rpc("thread/start", {"cwd": "/tmp", "approvalPolicy": "never",
            "sandbox": "read-only", "baseInstructions": "这是上下文测试。只回答问题，禁止调用任何工具。"})
        tid = thread["thread"]["id"]
        for index, (query, note) in enumerate((("不要输出本轮暗号，只回复收到。", "本轮私有暗号：青柠九号"),
                            ("当前参考包有什么暗号？只用当前参考作答。", "本轮私有暗号：海盐二号"),
                            ("最初一轮有个没有说出口的暗号，现在你还能看到吗？只在上下文确实有时复述，否则说未知。", "本轮参考为空"))):
            turn = await rpc("turn/start", {"threadId": tid, "input": [{"type": "text", "text": query}],
                "additionalContext": {"boxagent.query-reference": {"kind": "untrusted", "value": note}}})
            await asyncio.wait_for(finished.get(), 60)
            if index < 2:
                await rpc("thread/revert", {"threadId": tid, "beforeTurnId": turn["turn"]["id"]})
        data = await rpc("thread/read", {"threadId": tid, "includeTurns": True})
        path = Path("/tmp/boxagent-codex-reference.json")
        path.write_text(json.dumps({"thread": data, "events": observed}, ensure_ascii=False, indent=2))
        print(json.dumps({"same_thread": True, "additionalContext_accepted": True,
                          "evidence": str(path)}, ensure_ascii=False))
    finally:
        process.terminate()
        await process.wait()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
