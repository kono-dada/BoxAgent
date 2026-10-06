"""Jev-Mem 子进程适配器；桌宠主进程不直接导入重型模型依赖。"""

import asyncio
import contextlib
import json
import os
from pathlib import Path

from boxagent.domain.memory.contracts import MemoryUnavailable


class JevMemoryWorker:
    """Run Jev-Mem behind a serialized JSONL request/response boundary."""

    def __init__(self, *, python=None, source=None, cache_dir=None, backend="auto",
                 worker_script=None, workspace=None, typesafe_api_key="",
                 startup_timeout=90, request_timeout=30, command=None, log_path=None):
        workspace = Path(workspace or Path.cwd())
        self.python = Path(python or workspace / ".runtime/jev-mem-venv/bin/python").expanduser()
        self.source = Path(source or workspace / ".runtime/jev-mem-src").expanduser()
        self.cache_dir = Path(cache_dir or workspace / ".runtime/pet/memory/jev").expanduser()
        self.backend = backend
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.command = list(command) if command else None
        self.worker_script = Path(worker_script or workspace / "scripts/jev_memory_worker.py")
        self.workspace = workspace
        self.typesafe_api_key = typesafe_api_key
        self.log_path = Path(log_path or workspace / ".runtime/pet/memory-worker.log")
        self.process = None
        self.stderr = None
        self.sequence = 0
        self.lock = asyncio.Lock()

    def _command(self):
        if self.command:
            return self.command
        if not self.python.is_file():
            raise MemoryUnavailable(f"Jev-Mem Python 不存在：{self.python}")
        if not (self.source / "jev_mem").is_dir():
            raise MemoryUnavailable(f"Jev-Mem 源码不存在：{self.source}")
        return [str(self.python), str(self.worker_script),
                "--source", str(self.source), "--cache-dir", str(self.cache_dir),
                "--backend", self.backend]

    def _environment(self):
        names = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "HF_HOME",
                 "HF_HUB_CACHE", "TRANSFORMERS_CACHE", "XDG_CACHE_HOME",
                 "HF_ENDPOINT", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
                 "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")
        env = {name: os.environ[name] for name in names if name in os.environ}
        if self.typesafe_api_key:
            env["TYPESAFE_API_KEY"] = self.typesafe_api_key
        elif self.backend == "jev":
            raise MemoryUnavailable("BOXAGENT_JEV_MEMORY_BACKEND=jev 时需要 TYPESAFE_API_KEY")
        env.update(PYTHONUNBUFFERED="1", TOKENIZERS_PARALLELISM="false")
        return env

    async def _start_unlocked(self):
        if self.process and self.process.returncode is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stderr = self.log_path.open("ab")
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self._command(), cwd=self.workspace, env=self._environment(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=self.stderr,
            )
        except Exception:
            self.stderr.close()
            self.stderr = None
            raise

    async def _stop_unlocked(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if process and process.stdin and not process.stdin.is_closing():
            process.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await process.stdin.wait_closed()
        if process and process.stdout:
            with contextlib.suppress(Exception):
                await process.stdout.read()
        if self.stderr:
            self.stderr.close()
            self.stderr = None

    async def _request(self, operation, **payload):
        async with self.lock:
            await self._start_unlocked()
            self.sequence += 1
            request_id = self.sequence
            request = {"id": request_id, "operation": operation, **payload}
            timeout = self.startup_timeout if request_id == 1 else self.request_timeout
            try:
                self.process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode())
                await self.process.stdin.drain()
                line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
                if not line:
                    returncode = self.process.returncode
                    await self._stop_unlocked()
                    raise MemoryUnavailable(f"Memory Worker 已退出：{returncode}")
                response = json.loads(line)
                if response.get("id") != request_id:
                    await self._stop_unlocked()
                    raise MemoryUnavailable("Memory Worker 返回了错位的请求 ID")
                if not response.get("ok"):
                    error = response.get("error") or {}
                    raise MemoryUnavailable(error.get("message") or "Memory Worker 请求失败")
                return response.get("result", {})
            except (TimeoutError, BrokenPipeError, json.JSONDecodeError) as exc:
                await self._stop_unlocked()
                raise MemoryUnavailable(f"Memory Worker 协议失败：{type(exc).__name__}") from exc
            except asyncio.CancelledError:
                # A caller-level hard timeout must not leave an unread response
                # in stdout, otherwise the next request would consume a stale ID.
                await self._stop_unlocked()
                raise

    async def health(self):
        return await self._request("health")

    async def remember(self, observations):
        if not isinstance(observations, list) or not observations:
            raise ValueError("observations 必须是非空列表")
        return await self._request("remember", observations=observations)

    async def query(self, question, *, top_k=5):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question 必须是非空文本")
        if type(top_k) is not int or not 1 <= top_k <= 50:
            raise ValueError("top_k 必须在 1 到 50 之间")
        return await self._request("query", question=question, top_k=top_k)

    async def inspect(self, *, query="", selected_id=None, node_limit=100, edge_limit=200):
        if not isinstance(query, str) or len(query) > 500:
            raise ValueError("query 必须是不超过 500 个字符的文本")
        if selected_id is not None and (not isinstance(selected_id, str)
                                        or not selected_id.strip()
                                        or len(selected_id) > 200):
            raise ValueError("selected_id 必须是不超过 200 个字符的非空 ID")
        if type(node_limit) is not int or not 1 <= node_limit <= 200:
            raise ValueError("node_limit 必须在 1 到 200 之间")
        if type(edge_limit) is not int or not 0 <= edge_limit <= 500:
            raise ValueError("edge_limit 必须在 0 到 500 之间")
        return await self._request("inspect", query=query,
                                   selected_id=selected_id.strip() if selected_id else None,
                                   node_limit=node_limit, edge_limit=edge_limit)

    async def forget(self, memory_ids):
        if (not isinstance(memory_ids, list) or not memory_ids or len(memory_ids) > 50
                or any(not isinstance(item, str) or not item.strip() or len(item) > 200
                       for item in memory_ids)):
            raise ValueError("memory_ids 必须是 1 到 50 个非空记忆 ID")
        unique_ids = list(dict.fromkeys(item.strip() for item in memory_ids))
        return await self._request("forget", memory_ids=unique_ids)

    async def save(self):
        return await self._request("save")

    async def close(self):
        async with self.lock:
            if not self.process:
                return
            if self.process.returncode is None:
                self.sequence += 1
                request_id = self.sequence
                try:
                    self.process.stdin.write((json.dumps({"id": request_id, "operation": "shutdown"}) + "\n").encode())
                    await self.process.stdin.drain()
                    await asyncio.wait_for(self.process.stdout.readline(), 5)
                    self.process.stdin.close()
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                        await self.process.stdin.wait_closed()
                    await asyncio.wait_for(self.process.wait(), 5)
                    await self.process.stdout.read()
                except (TimeoutError, BrokenPipeError):
                    await self._stop_unlocked()
                    return
            await self._stop_unlocked()
