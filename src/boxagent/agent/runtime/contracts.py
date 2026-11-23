"""Stable ports implemented by Agent Runtimes."""

from asyncio.subprocess import Process
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from boxagent.agent.runtime.models import RuntimeMessage, RuntimeRequest
from boxagent.core.states import Approval
from boxagent.domain.events.models import EventDecision

type ToolPayload = dict[str, Any]

ToolCall = Callable[[str, ToolPayload], Awaitable[ToolPayload]]
ResultValidator = Callable[[str], ToolPayload]
Progress = Callable[[str], None]


class EventAgent(Protocol):
    async def evaluate(self, request: RuntimeRequest) -> EventDecision: ...


class ToolObservationSink(Protocol):
    async def __call__(
        self, operation: str, arguments: ToolPayload, response: ToolPayload
    ) -> None: ...


class AgentRuntime(Protocol):
    """Run one agentic reasoning/tool loop behind a provider-neutral port."""

    provider: str
    model: str

    async def execute(self, request: RuntimeRequest, tools: list[ToolPayload], call_tool: ToolCall,
                      validate_result: ResultValidator, progress: Progress) -> str: ...


class ComputerUseSession(Protocol):
    @property
    def process(self) -> Process | None: ...

    @property
    def thread_id(self) -> str | None: ...

    @property
    def cleanup_status(self) -> str: ...

    async def start(self) -> list[ToolPayload]: ...
    async def call_tool(self, operation: str, arguments: ToolPayload) -> ToolPayload: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...


class StructuredSession(Protocol):
    async def start(self) -> object: ...

    async def run_isolated_structured_turn(
        self, *, model: str, instructions: str, query: str, output_schema: dict[str, object]
    ) -> str: ...

    async def close(self) -> None: ...


class SessionLog(Protocol):
    def __call__(self, event: str, /, **data: object) -> None: ...


class StructuredSessionFactory(Protocol):
    def __call__(
        self, *, output: None, on_tool_call: ToolCall, approve: Approval,
        current_app: Callable[[], str], report: Callable[[str, str], None],
        log: SessionLog, auto_approve: bool,
    ) -> StructuredSession: ...


class TaskTurnSession(ComputerUseSession, StructuredSession, Protocol):
    tools: list[ToolPayload]

    async def run_codex_turn(
        self, *, model: str, instructions: str, query: str, output_schema: dict[str, object],
        history: Sequence[RuntimeMessage] = (), history_delta: Sequence[RuntimeMessage] = (),
        evidence_context: str = "",
    ) -> str: ...


async def deny_tool_approval(_request: ToolPayload) -> bool:
    return False
