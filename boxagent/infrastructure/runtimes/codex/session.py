"""Lease task-scoped sessions from one long-lived Codex App Server."""

import asyncio
import contextlib
from pathlib import Path

from boxagent.infrastructure.runtimes.codex.app_server import CodexAppServer


class CodexTaskSession:
    """A task-local view over the shared App Server and Codex thread."""

    def __init__(self, host, callbacks, *, product_session_id=None,
                 runtime_thread_id=None, on_thread_bound=None):
        self.host = host
        self.callbacks = callbacks
        self.product_session_id = product_session_id
        self.runtime_thread_id = runtime_thread_id
        self.on_thread_bound = on_thread_bound
        self.server = None
        self.acquired = False

    @property
    def process(self):
        return self.server.process if self.server else None

    @property
    def cleanup_status(self):
        return self.server.cleanup_status if self.server else "not_started"

    @property
    def thread_id(self):
        return self.server.thread_id if self.server else None

    @property
    def tools(self):
        return self.server.tools if self.server else []

    @tools.setter
    def tools(self, value):
        if self.server is None:
            raise RuntimeError("Codex Runtime 尚未启动")
        self.server.tools = value

    async def start(self):
        self.server = await self.host.acquire(self.callbacks)
        self.acquired = True
        return self.server.discovered_tools

    async def call_tool(self, operation, arguments):
        return await self.server.call_tool(operation, arguments)

    async def run_codex_turn(self, **arguments):
        return await self.server.run_codex_turn(
            preferred_thread_id=self.runtime_thread_id,
            on_thread_bound=self.on_thread_bound,
            product_session_id=self.product_session_id,
            **arguments)

    async def run_isolated_structured_turn(self, **arguments):
        return await self.server.run_isolated_structured_turn(**arguments)

    async def interrupt(self):
        if self.server:
            await self.server.interrupt()

    async def close(self):
        if self.acquired:
            self.acquired = False
            await self.host.release(self.server)


class CodexRuntimeHost:
    """Application-scoped owner of the Codex process and reusable thread."""

    def __init__(self, *, output, workspace, model_profile, permission_check,
                 codex_home=None,
                 skill_service=None,
                 runtime_resolver=None, server_factory=CodexAppServer):
        self.output = Path(output)
        self.workspace = Path(workspace)
        self.model_profile = model_profile
        self.permission_check = permission_check
        self.codex_home = Path(codex_home) if codex_home else None
        self.skill_service = skill_service
        self.runtime_resolver = runtime_resolver
        self.server_factory = server_factory
        self.server = None
        self.lock = asyncio.Lock()
        self.closed = False

    def create_session(self, **callbacks):
        # Task artifacts are owned by the Harness. The shared process keeps its
        # own stderr under the application-scoped runtime directory.
        callbacks.pop("output", None)
        product_session_id = callbacks.pop("product_session_id", None)
        runtime_thread_id = callbacks.pop("runtime_thread_id", None)
        on_thread_bound = callbacks.pop("on_thread_bound", None)
        return CodexTaskSession(
            self, callbacks, product_session_id=product_session_id,
            runtime_thread_id=runtime_thread_id,
            on_thread_bound=on_thread_bound)

    async def start(self):
        """Lifecycle hook; the expensive process launch remains lazy."""
        if self.closed:
            raise RuntimeError("Codex Runtime 已关闭")

    async def acquire(self, callbacks):
        await self.lock.acquire()
        try:
            if self.closed:
                raise RuntimeError("Codex Runtime 已关闭")
            if self.server is None or self.server.process is None \
                    or self.server.process.returncode is not None:
                if self.server is not None:
                    await self.server.close()
                self.server = self.server_factory(
                    output=self.output,
                    workspace=self.workspace,
                    model_profile=self.model_profile,
                    permission_check=self.permission_check,
                    codex_home=self.codex_home,
                    skill_service=self.skill_service,
                    runtime_resolver=self.runtime_resolver,
                    **callbacks,
                )
            else:
                self.server.bind_task(**callbacks)
            await self.server.start()
            return self.server
        except BaseException:
            if self.server is not None:
                with contextlib.suppress(Exception):
                    await self.server.close()
                self.server = None
            if self.lock.locked():
                self.lock.release()
            raise

    async def release(self, server):
        try:
            if server is self.server:
                await server.release_task()
        finally:
            if self.lock.locked():
                self.lock.release()

    async def sync_skills(self):
        async with self.lock:
            if self.closed:
                raise RuntimeError("Codex Runtime 已关闭")
            if self.server is None or self.server.process is None \
                    or self.server.process.returncode is not None:
                return []
            return await self.server.refresh_skills(force=True)

    async def close(self):
        if self.closed:
            return
        self.closed = True
        async with self.lock:
            if self.server:
                await self.server.close()


def create_codex_session(**callbacks):
    """Create an isolated session for tests and standalone task execution."""
    return CodexAppServer(**callbacks)
