"""Application task service: acceptance, execution, approval, and cancellation."""

import asyncio
import re
import time

from boxagent.core.errors import TaskFailure
from boxagent.core.ids import new_task_id


class ExecutionService:
    def __init__(self, events, executor_factory, *, conversation=None,
                 on_terminal=None):
        self.events = events
        self.executor_factory = executor_factory
        self.conversation = conversation
        self.on_terminal = on_terminal
        self.job = self.executor = None
        self.interaction = None
        self.approval_future = None
        self.approved_requests = set()
        self.last_result = {"status": "idle"}

    @property
    def state(self):
        return self.events.state

    async def start(self, goal, *, from_text=False, interaction=None):
        if not isinstance(goal, str) or not goal.strip():
            return {"status": "failed", "message": "请输入要完成的事情"}
        if self.job and not self.job.done():
            return {"status": "busy", "message": "请等待当前任务结束，或先停止任务"}
        task_id = new_task_id()
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

    async def _run(self, goal, interaction=None, task_id=None):
        task_id = task_id or self.state.task_id or new_task_id()
        self.events.emit("task.started", task="running")
        monitor = asyncio.create_task(self._monitor())
        try:
            result = await self.executor.run(goal, self.progress, self.approve)
            if self.state.task == "cancelling":
                raise asyncio.CancelledError
            status = "succeeded" if result["outcome"] == "completed" else result["outcome"]
            self.last_result = {"status": status, "task_id": self.state.task_id,
                                "goal": goal, **result}
            self.events.emit("task." + status, task=status, task_text=result["summary"],
                             assistant_text=result["summary"], approval="",
                             task_ended_at=time.time())
            if interaction:
                await self.conversation.finish_interaction(
                    interaction, status=status, assistant_content=result["summary"],
                    task_id=task_id,
                    data={key: result[key] for key in
                          ("outcome", "assessment", "steps", "error_code")
                          if key in result})
            await self._notify_terminal(
                interaction, task_id, status, result["summary"])
        except asyncio.CancelledError:
            self.last_result = {"status": "cancelled",
                                "message": "已停止后续操作；已发生的操作不会撤销。"}
            self.events.emit("task.cancelled", task="cancelled",
                             task_text="任务已停止；已发生的操作不会撤销",
                             assistant_text="任务已停止；已发生的操作不会撤销",
                             approval="", task_ended_at=time.time())
            if interaction:
                await self.conversation.finish_interaction(
                    interaction, status="cancelled",
                    assistant_content=self.last_result["message"],
                    task_id=task_id)
            await self._notify_terminal(
                interaction, task_id, "cancelled", self.last_result["message"])
        except Exception as exc:
            message = (str(exc) if isinstance(exc, TaskFailure) else
                       "执行超时，任务已结束，请稍后重试" if isinstance(exc, TimeoutError) else
                       "执行出现异常，任务已结束，请稍后重试")
            self.last_result = {"status": "failed", "task_id": self.state.task_id,
                                "message": message,
                                "error_code": getattr(exc, "code", "execution_error")}
            self.events.emit("task.failed", task="failed", task_text=message,
                             assistant_text=message, approval="", task_ended_at=time.time())
            if interaction:
                await self.conversation.finish_interaction(
                    interaction, status="failed", assistant_content=message,
                    task_id=task_id,
                    data={"error_code": self.last_result["error_code"]})
            await self._notify_terminal(interaction, task_id, "failed", message)
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        return self.last_result

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

    async def cancel(self):
        if not self.job or self.job.done():
            return
        self.events.emit("task.cancelling", task="cancelling",
                         task_text="正在停止后续操作…")
        await self.answer_approval(False)
        await self.executor.cancel()
        self.job.cancel()
        await asyncio.gather(self.job, return_exceptions=True)
        if self.state.task == "cancelling":
            self.last_result = {"status": "cancelled", "message": "任务已停止"}
            self.events.emit("task.cancelled", task="cancelled", task_text="任务已停止",
                             approval="", task_ended_at=time.time())

    async def close(self):
        await self.cancel()
