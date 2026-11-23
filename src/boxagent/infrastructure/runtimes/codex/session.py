"""Lease task-scoped sessions from one long-lived Codex App Server."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, Unpack

from boxagent.agent.runtime.contracts import ToolCall, ToolObservationSink, ToolPayload
from boxagent.agent.runtime.models import ModelProfile, RuntimeMessage
from boxagent.domain.skill import SkillService
from boxagent.infrastructure.runtimes.codex.app_server import CodexAppServer
from boxagent.infrastructure.runtimes.codex.windows_skill import WindowsSkill

type _Approve = Callable[[ToolPayload], Awaitable[bool]]
type _ContextSynced = Callable[[Sequence[RuntimeMessage]], object]


class RuntimeLog(Protocol):
    def __call__(self, event: str, /, **data: object) -> None: ...


class PermissionCheck(Protocol):
    def __call__(self, params: ToolPayload, /, *, stopped: bool = False) -> bool: ...


class RuntimeServerFactory(Protocol):
    def __call__(self, *, output: Path, workspace: Path, model_profile: ModelProfile,
                 permission_check: PermissionCheck, codex_home: Path | None,
                 skill_service: SkillService | None, runtime_resolver: Callable[[], Path] | None,
                 on_tool_call: ToolCall, approve: _Approve,
                 current_app: Callable[[], str], report: Callable[[str, str], None],
                 log: RuntimeLog, auto_approve: bool = True,
                 on_tool_observed: ToolObservationSink | None = None) -> CodexAppServer: ...


class _TaskCallbacks(TypedDict):
    on_tool_call: ToolCall
    approve: _Approve
    current_app: Callable[[], str]
    report: Callable[[str, str], None]
    log: RuntimeLog
    auto_approve: NotRequired[bool]
    on_tool_observed: NotRequired[ToolObservationSink | None]


class _StructuredTurn(TypedDict):
    model: str
    instructions: str
    query: str
    output_schema: dict[str, object]


class _TaskTurn(_StructuredTurn, total=False):
    history: Sequence[RuntimeMessage]
    history_delta: Sequence[RuntimeMessage]
    evidence_context: str


class _StandaloneSession(_TaskCallbacks):
    output: Path
    runtime_resolver: NotRequired[Callable[[], Path] | None]
    workspace: NotRequired[str | Path]
    model_profile: NotRequired[ModelProfile | None]
    permission_check: NotRequired[PermissionCheck | None]
    codex_home: NotRequired[str | Path | None]
    skill_service: NotRequired[SkillService | None]
    windows_skill_loader: NotRequired[Callable[[], WindowsSkill] | None]
    computer_use_enabled: NotRequired[bool]


class CodexTaskSession:
    """A task-local view over the shared App Server and Codex thread."""

    def __init__(
        self,
        host: CodexRuntimeHost,
        callbacks: _TaskCallbacks,
        *,
        product_session_id: str | None = None,
        runtime_thread_id: str | None = None,
        on_thread_bound: Callable[[str], object] | None = None,
        on_context_synced: _ContextSynced | None = None,
    ) -> None:
        self.host = host
        self.callbacks = callbacks
        self.product_session_id = product_session_id
        self.runtime_thread_id = runtime_thread_id
        self.on_thread_bound = on_thread_bound
        self.on_context_synced = on_context_synced
        self.server: CodexAppServer | None = None
        self.acquired = False

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        return self.server.process if self.server else None

    @property
    def cleanup_status(self) -> str:
        return self.server.cleanup_status if self.server else "not_started"

    @property
    def thread_id(self) -> str | None:
        return self.server.thread_id if self.server else None

    @property
    def tools(self) -> list[ToolPayload]:
        return self.server.tools if self.server else []

    @tools.setter
    def tools(self, value: list[ToolPayload]) -> None:
        if self.server is None:
            raise RuntimeError("Codex Runtime 尚未启动")
        self.server.tools = value

    async def start(self) -> list[ToolPayload]:
        self.server = await self.host.acquire(self.callbacks)
        self.acquired = True
        return self.server.discovered_tools

    async def call_tool(self, operation: str, arguments: ToolPayload) -> ToolPayload:
        if self.server is None:
            raise RuntimeError("Codex Runtime 尚未启动")
        return await self.server.call_tool(operation, arguments)

    async def run_codex_turn(self, **arguments: Unpack[_TaskTurn]) -> str:
        if self.server is None:
            raise RuntimeError("Codex Runtime 尚未启动")
        return await self.server.run_codex_turn(
            preferred_thread_id=self.runtime_thread_id,
            on_thread_bound=self.on_thread_bound,
            on_context_synced=self.on_context_synced,
            product_session_id=self.product_session_id,
            **arguments)

    async def run_isolated_structured_turn(self, **arguments: Unpack[_StructuredTurn]) -> str:
        if self.server is None:
            raise RuntimeError("Codex Runtime 尚未启动")
        return await self.server.run_isolated_structured_turn(**arguments)

    async def interrupt(self) -> None:
        if self.server:
            await self.server.interrupt()

    async def close(self) -> None:
        if self.acquired:
            self.acquired = False
            await self.host.release(self.server)


class CodexRuntimeHost:
    """Application-scoped owner of the Codex process and reusable thread."""

    def __init__(
        self,
        *,
        output: str | Path,
        workspace: str | Path,
        model_profile: ModelProfile,
        permission_check: PermissionCheck,
        codex_home: str | Path | None = None,
        skill_service: SkillService | None = None,
        runtime_resolver: Callable[[], Path] | None = None,
        server_factory: RuntimeServerFactory = CodexAppServer,
    ) -> None:
        self.output = Path(output)
        self.workspace = Path(workspace)
        self.model_profile = model_profile
        self.permission_check = permission_check
        self.codex_home = Path(codex_home) if codex_home else None
        self.skill_service = skill_service
        self.runtime_resolver = runtime_resolver
        self.server_factory = server_factory
        self.server: CodexAppServer | None = None
        self.lock = asyncio.Lock()
        self.closed = False

    def create_session(
        self,
        *,
        output: Path | None = None,
        product_session_id: str | None = None,
        runtime_thread_id: str | None = None,
        on_thread_bound: Callable[[str], object] | None = None,
        on_context_synced: _ContextSynced | None = None,
        **callbacks: Unpack[_TaskCallbacks],
    ) -> CodexTaskSession:
        # Task artifacts are owned by the Harness. The shared process keeps its
        # own stderr under the application-scoped runtime directory.
        del output
        return CodexTaskSession(
            self, callbacks, product_session_id=product_session_id,
            runtime_thread_id=runtime_thread_id,
            on_thread_bound=on_thread_bound, on_context_synced=on_context_synced)

    async def start(self) -> None:
        """Lifecycle hook; the expensive process launch remains lazy."""
        if self.closed:
            raise RuntimeError("Codex Runtime 已关闭")

    async def acquire(self, callbacks: _TaskCallbacks) -> CodexAppServer:
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

    async def release(self, server: CodexAppServer | None) -> None:
        try:
            if server is not None and server is self.server:
                await server.release_task()
        finally:
            if self.lock.locked():
                self.lock.release()

    async def sync_skills(self) -> list[ToolPayload]:
        async with self.lock:
            if self.closed:
                raise RuntimeError("Codex Runtime 已关闭")
            if self.server is None or self.server.process is None \
                    or self.server.process.returncode is not None:
                return []
            return await self.server.refresh_skills(force=True)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        async with self.lock:
            if self.server:
                await self.server.close()


def create_codex_session(**callbacks: Unpack[_StandaloneSession]) -> CodexAppServer:
    """Create an isolated session for tests and standalone task execution."""
    return CodexAppServer(**callbacks)
