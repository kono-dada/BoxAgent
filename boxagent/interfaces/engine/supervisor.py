"""Own the restartable Engine subprocess without owning native UI state."""

import asyncio
import os
import sys


class EngineSupervisor:
    def __init__(self, *, root, socket_path, log_path, task_provider, task_model=None,
                 auto_approve=True, context_interval=15, context_size=960):
        self.root = root
        self.socket_path = socket_path
        self.log_path = log_path
        self.task_provider = task_provider
        self.task_model = task_model
        self.auto_approve = auto_approve
        self.context_interval = context_interval
        self.context_size = context_size
        self.process = None
        self.log_stream = None

    async def start(self):
        if self.process and self.process.returncode is None:
            return self.process
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_stream = self.log_path.open("ab", buffering=0)
        command = [
            sys.executable, "-m", "boxagent.entrypoints.engine",
            "--socket", str(self.socket_path),
            "--log-dir", str(self.log_path.parent),
            "--task-provider", self.task_provider,
            "--context-interval", str(self.context_interval),
            "--context-size", str(self.context_size),
        ]
        if self.task_model:
            command.extend(("--task-model", self.task_model))
        if not self.auto_approve:
            command.append("--require-approval")
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.root,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=self.log_stream,
            stderr=self.log_stream,
        )
        return self.process

    async def stop(self, timeout=8):
        process, self.process = self.process, None
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout)
            except TimeoutError:
                process.kill()
                await process.wait()
        if self.log_stream:
            self.log_stream.close()
            self.log_stream = None

    async def restart(self):
        await self.stop()
        return await self.start()
