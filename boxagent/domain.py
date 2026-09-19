"""纯数据契约；不依赖 AppKit、模型 SDK 或角色素材。"""

from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Protocol


@dataclass
class Snapshot:
    voice: str = "off"
    speaking: bool = False
    user_speaking: bool = False
    task: str = "idle"
    task_id: str = ""
    task_started_at: float = 0
    task_ended_at: float = 0
    task_activity_at: float = 0
    task_text: str = ""
    user_text: str = ""
    assistant_text: str = ""
    error: str = ""
    approval: str = ""
    context_text: str = ""
    context_enabled: bool = False
    context_status: str = ""
    context_app: str = ""
    context_at: float = 0
    revision: int = 0

    def payload(self):
        return asdict(self)


Emit = Callable[..., None]
Approval = Callable[[dict], Awaitable[bool]]


class Executor(Protocol):
    async def run(self, goal: str, progress: Callable[[str], None],
                  approve: Approval) -> dict: ...

    async def cancel(self) -> None: ...


class Voice(Protocol):
    async def run(self) -> None: ...

    async def stop(self) -> None: ...


def presentation_state(state: Snapshot) -> str:
    """交互优先；后台任务状态仍独立保存在快照里。"""
    if state.approval:
        return "approval"
    if state.user_speaking:
        return "listening"
    if state.speaking:
        return "speaking"
    if state.task in {"accepted", "running", "cancelling"}:
        return "working"
    if state.voice == "connecting":
        return "connecting"
    if state.error or state.task in {"failed", "blocked"}:
        return "error"
    if state.task == "succeeded":
        return "success"
    if state.voice == "ready":
        return "listening"
    return "idle"
