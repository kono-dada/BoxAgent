"""Application task service: acceptance, execution, approval, and cancellation."""

import asyncio
import re
import time
from collections import deque
from collections.abc import Callable
from typing import TypedDict

from boxagent.core.errors import TaskFailure
from boxagent.core.ids import new_task_id
from boxagent.core.states import Executor, TaskQueueItem
from boxagent.domain.conversation.models import InteractionContext


class QueuedTask(TypedDict):
    goal: str
    from_text: bool
    interaction: InteractionContext | None
    task_id: str


class ExecutionService:
    def __init__(self, events, executor_factory: Callable[..., Executor], *,
                 conversation=None,
                 on_terminal=None):
        self.events = events
        self.executor_factory = executor_factory
        self.conversation = conversation
        self.on_terminal = on_terminal
        self.job = None
        self.executor: Executor | None = None
        self.interaction = None
        self.approval_future = None
        self.approved_requests = set()
        self.last_result = {"status": "idle"}
        self.pending: deque[QueuedTask] = deque()
        self.start_lock = asyncio.Lock()
        self.closed = False

    @property
    def state(self):
        return self.events.state

    async def start(self, goal, *, from_text=False, interaction=None):
        if not isinstance(goal, str) or not goal.strip():
            return {"status": "failed", "message": "请输入要完成的事情"}
        task_id = new_task_id()
        async with self.start_lock:
            if ((self.job and not self.job.done()) or self.pending):
                self.pending.append({
                    "goal": goal, "from_text": from_text,
                    "interaction": interaction, "task_id": task_id,
                })
                self.events.emit("task.queued", task_queue=self._queue_snapshot())
                result = {
                    "status": "queued", "task_id": task_id,
                    "position": len(self.pending),
                    "message": f"任务已排队，前面还有 {len(self.pending)} 个任务",
                }
                if interaction:
                    result.update(
                        session_id=interaction.session.session_id,
                        interaction_id=interaction.interaction_id)
                return result
            return await self._start_now(
                goal, from_text=from_text, interaction=interaction,
                task_id=task_id)

    async def _start_now(self, goal, *, from_text=False, interaction=None,
                         task_id=None):
        task_id = task_id or new_task_id()
        self.interaction = interaction
        context = {}
        if self.conversation and self.conversation.store and self.interaction is None:
            self.interaction = await self.conversation.begin_interaction(
                goal, task_id=task_id, source="text" if from_text else "voice",
                runtime="codex")
        if self.conversation and self.conversation.store and self.interaction is not None:
            self.interaction = await self.conversation.for_runtime(
                self.interaction, "codex")
            context = {
                "session_id": self.interaction.session.session_id,
                "interaction_id": self.interaction.interaction_id,
                "prior_messages": self.interaction.prior_messages,
                "runtime_binding": self.interaction.runtime_binding,
            }
        self.executor = self.executor_factory(task_id, **context)
        changes = {"user_text": goal, "assistant_text": ""} if from_text else {}
        self.events.emit("task.accepted", task="accepted", task_id=task_id,
                         task_goal=goal, task_queue=self._queue_snapshot(),
                         task_text="正在准备…", error="", task_started_at=time.time(),
                         task_ended_at=0, task_activity_at=time.time(), **changes)
        self.last_result = {"status": "running", "task_id": task_id, "goal": goal}
        self.job = asyncio.create_task(self._run(goal, self.interaction, task_id))
        result = {"status": "accepted", "task_id": task_id}
        if self.interaction:
            result.update(session_id=self.interaction.session.session_id,
                          interaction_id=self.interaction.interaction_id)
        return result

    async def submit_text(self, goal):
        return await self.start(goal, from_text=True)

    def _queue_snapshot(self) -> list[TaskQueueItem]:
        return [{"task_id": item["task_id"], "goal": item["goal"]}
                for item in self.pending]

    async def _run(self, goal, interaction=None, task_id=None):
        task_id = task_id or self.state.task_id or new_task_id()
        terminal_result = None
        self.events.emit("task.started", task="running")
        monitor = asyncio.create_task(self._monitor())
        try:
            executor = self.executor
            if executor is None:
                raise RuntimeError("任务执行器尚未创建")
            result = await executor.run(goal, self.progress, self.approve)
            if self.state.task == "cancelling":
                raise asyncio.CancelledError
            status = "succeeded" if result["outcome"] == "completed" else result["outcome"]
            self.last_result = {"status": status, "task_id": self.state.task_id,
                                "goal": goal, **result}
            terminal_result = dict(self.last_result)
            self.events.emit("task." + status, task=status, task_text=result["summary"],
                             assistant_text=result["summary"], approval="",
                             task_ended_at=time.time())
            if interaction and self.conversation:
                await self.conversation.finish_interaction(
                    interaction, status=status, assistant_content=result["summary"],
                    task_id=task_id,
                    data={key: result[key] for key in
                          ("outcome", "assessment", "steps", "error_code")
                          if key in result})
                self.events.emit("conversation.updated")
            await self._notify_terminal(
                interaction, task_id, status, result["summary"])
        except asyncio.CancelledError:
            terminal_result = await self._finish_cancelled(interaction, task_id)
        except Exception as exc:
            message = (str(exc) if isinstance(exc, TaskFailure) else
                       "执行超时，任务已结束，请稍后重试" if isinstance(exc, TimeoutError) else
                       "执行出现异常，任务已结束，请稍后重试")
            self.last_result = {"status": "failed", "task_id": self.state.task_id,
                                "message": message,
                                "error_code": getattr(exc, "code", "execution_error")}
            terminal_result = dict(self.last_result)
            self.events.emit("task.failed", task="failed", task_text=message,
                             assistant_text=message, approval="", task_ended_at=time.time())
            if interaction and self.conversation:
                await self.conversation.finish_interaction(
                    interaction, status="failed", assistant_content=message,
                    task_id=task_id,
                    data={"error_code": self.last_result["error_code"]})
                self.events.emit("conversation.updated")
            await self._notify_terminal(interaction, task_id, "failed", message)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await self._start_next(asyncio.current_task())
        return terminal_result or self.last_result

    async def _finish_cancelled(self, interaction: InteractionContext | None,
                               task_id: str) -> dict[str, object]:
        message = "已停止后续操作；已发生的操作不会撤销。"
        result: dict[str, object] = {"status": "cancelled", "task_id": task_id, "message": message}
        self.last_result = result
        self.events.emit("task.cancelled", task="cancelled",
                         task_text="任务已停止；已发生的操作不会撤销",
                         assistant_text="任务已停止；已发生的操作不会撤销",
                         approval="", task_ended_at=time.time())
        if interaction and self.conversation:
            await self.conversation.finish_interaction(
                interaction, status="cancelled", assistant_content=message,
                task_id=task_id)
            self.events.emit("conversation.updated")
        await self._notify_terminal(interaction, task_id, "cancelled", message)
        return result

    async def _start_next(self, finished_job: asyncio.Task[dict[str, object]] | None) -> None:
        async with self.start_lock:
            if self.job is not finished_job or self.closed or not self.pending:
                return
            item = self.pending.popleft()
            await self._start_now(**item)

    async def _cancel_pending(self, cancelled: list[QueuedTask]) -> None:
        """Finalize queued Product Interactions when the user cancels the queue."""
        for item in cancelled:
            interaction = item.get("interaction")
            task_id = item["task_id"]
            message = "排队任务已取消；未执行任何操作。"
            if interaction and self.conversation:
                await self.conversation.finish_interaction(
                    interaction, status="cancelled",
                    assistant_content=message, task_id=task_id)
                self.events.emit("conversation.updated")
            await self._notify_terminal(
                interaction, task_id, "cancelled", message)

    async def _notify_terminal(self, interaction, task_id, outcome, summary):
        if self.on_terminal is None:
            return
        try:
            result = self.on_terminal(
                interaction=interaction, task_id=task_id,
                outcome=outcome, summary=summary)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            self.events.emit(
                "notification.error", error="任务已经结束，但完成提醒暂时无法送达")

    async def _monitor(self):
        while True:
            await asyncio.sleep(5)
            self.events.emit("task.heartbeat")

    def progress(self, text):
        if self.state.task != "cancelling":
            self.events.emit("task.progress", task_text=text, task_activity_at=time.time())

    async def approve(self, params):
        if self.state.task == "cancelling":
            return False
        key = (params.get("serverName"), params.get("app"), params.get("message"))
        if key in self.approved_requests:
            return True
        self.approval_future = asyncio.get_running_loop().create_future()
        request_text = params.get("message", "")
        display_name = re.fullmatch(r"Allow ChatGPT to use (.+)\?", request_text)
        app_name = display_name.group(1) if display_name else params.get("app", "")
        message = f"允许操作 {app_name}？" if app_name else request_text or "允许这次工具请求？"
        self.events.emit("task.approval", task="awaiting_approval", approval=message)
        try:
            accepted = await asyncio.wait_for(self.approval_future, 55)
            if accepted:
                self.approved_requests.add(key)
            return accepted
        except TimeoutError:
            return False
        finally:
            self.approval_future = None
            self.events.emit("task.approval_resolved", approval="",
                             task="cancelling" if self.state.task == "cancelling" else "running")

    async def answer_approval(self, allowed):
        if self.approval_future and not self.approval_future.done():
            self.approval_future.set_result(allowed)

    async def cancel(self) -> dict[str, object]:
        """Stop the current task; queued tasks keep their FIFO order."""
        return await self._cancel(clear_pending=False)

    async def cancel_all(self) -> dict[str, object]:
        """Explicitly stop the current task and discard its waiting queue."""
        return await self._cancel(clear_pending=True)

    async def _cancel(self, *, clear_pending: bool) -> dict[str, object]:
        async with self.start_lock:
            cancelled = list(self.pending) if clear_pending else []
            if clear_pending:
                self.pending.clear()
                self.events.emit("task.queue_updated", task_queue=[])
            job, executor = self.job, self.executor
            interaction, task_id = self.interaction, self.state.task_id
            stopping = job is not None and not job.done()
            if stopping and job is not None:
                self.events.emit("task.cancelling", task="cancelling",
                                 task_text="正在停止后续操作…")
                await self.answer_approval(False)
                if executor is not None:
                    await executor.cancel()
                # Queue advancement also takes this lock: interruption must
                # finish before the next executor can use the shared runtime.
                job.cancel()
        result: dict[str, object] = {"status": "idle", "message": "当前没有正在执行的任务"}
        if stopping and job is not None:
            await asyncio.gather(job, return_exceptions=True)
            if job.cancelled():
                # Cancellation can arrive before _run starts, or while it is
                # waiting to advance the queue after recording its result.
                result = (await self._finish_cancelled(interaction, task_id)
                          if self.state.task_id == task_id and self.state.task == "cancelling"
                          else {"status": "cancelled", "task_id": task_id,
                                "message": "已停止后续操作；已发生的操作不会撤销。"})
            else:
                result = job.result()
            await self._start_next(job)
        await self._cancel_pending(cancelled)
        if clear_pending:
            result = {**result, "cancelled_queued": len(cancelled)}
            if stopping or cancelled:
                result.update(status="cancelled", message="已取消当前及排队任务；已发生的操作不会撤销。")
        return result

    async def close(self) -> None:
        self.closed = True
        await self.cancel_all()
