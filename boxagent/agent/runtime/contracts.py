"""Stable ports implemented by Agent Runtimes."""

from typing import Awaitable, Callable, Protocol

from boxagent.agent.runtime.models import RuntimeRequest


ToolCall = Callable[[str, dict], Awaitable[dict]]
ResultValidator = Callable[[str], dict]
Progress = Callable[[str], None]


class AgentRuntime(Protocol):
    """Run one agentic reasoning/tool loop behind a provider-neutral port."""

    provider: str
    model: str

    async def execute(self, request: RuntimeRequest, tools: list[dict], call_tool: ToolCall,
                      validate_result: ResultValidator, progress: Progress) -> str: ...


class ComputerUseSession(Protocol):
    tools: list[dict]
    process: object | None
    thread_id: str | None
    cleanup_status: str

    async def start(self) -> list[dict]: ...
    async def call_tool(self, operation: str, arguments: dict) -> dict: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
