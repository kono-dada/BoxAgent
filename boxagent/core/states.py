"""Immutable-shape application state and presentation projection."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, Protocol, TypedDict


class VoiceState(StrEnum):
    OFF = "off"
    CONNECTING = "connecting"
    READY = "ready"
    STOPPING = "stopping"


class TaskState(StrEnum):
    IDLE = "idle"
    ACCEPTED = "accepted"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskQueueItem(TypedDict):
    task_id: str
    goal: str


@dataclass
class Snapshot:
    session_id: str = ""
    session_title: str = ""
    voice: str = "off"
    speaking: bool = False
    user_speaking: bool = False
    task: str = "idle"
    task_id: str = ""
    task_goal: str = ""
    task_queue: list[TaskQueueItem] = field(default_factory=list)
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
    context_window_id: int = 0
    context_at: float = 0
    notification_id: str = ""
    notification_text: str = ""
    notification_unread: bool = False
    camera_status: str = "off"
    camera_error: str = ""
    camera_frame_at: float = 0
    camera_frames_sent: int = 0
    voice_input_level: float = -1
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
