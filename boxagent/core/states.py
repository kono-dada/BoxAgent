"""Immutable-shape application state and presentation projection."""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Awaitable, Callable, Literal, Protocol, TypedDict


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


class WebReference(TypedDict):
    title: str
    url: str
    published_at: str | None
    site_name: str | None
    read_status: Literal["not_read", "reading", "read", "unavailable"]


class WebResearchView(TypedDict):
    """JSON snapshot shape, independent of provider and feature implementations."""
    session_id: str
    interaction_id: str
    stage: Literal["searching", "reading", "ready", "empty", "failed", "cancelled"]
    query: str
    requested_version: str
    message: str
    retrieved_at: str
    sources: list[WebReference]


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
    web_research: WebResearchView | None = None
    error: str = ""
    approval: str = ""
    context_text: str = ""
    context_enabled: bool = False
    context_status: str = ""
    context_app: str = ""
    context_window_id: int = 0
    context_at: float = 0
    game_news_text: str = ""
    game_news_url: str = ""
    game_companion_available: bool = False
    game_companion_enabled: bool = False
    notification_id: str = ""
    notification_text: str = ""
    notification_unread: bool = False
    camera_status: str = "off"
    camera_error: str = ""
    camera_frame_at: float = 0
    camera_frames_sent: int = 0
    voice_input_level: float = -1
    avatar_action: str = ""
    avatar_action_id: str = ""
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

    async def say_farewell(self) -> None: ...


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
    return "idle"
