"""事件与任务生命周期；此层不知道窗口、动画行或音频设备。"""

import asyncio
import contextlib
import json
import re
import time
import uuid

from .domain import Snapshot
from .diagnostics import TaskFailure


class Runtime:
    def __init__(self, publish, executor_factory, voice_factory):
        self.state = Snapshot()
        self.publish = publish
        self.executor_factory, self.voice_factory = executor_factory, voice_factory
        self.voice = self.voice_task = self.job = self.executor = None
        self.approval_future = None
        self.approved_requests = set()
        self.last_result = {"status": "idle"}
        self.voice_toggle_lock = asyncio.Lock()
        self.observer = None
        self.context_toggle_lock = asyncio.Lock()

    async def toggle_context(self):
        async with self.context_toggle_lock:
            await self._toggle_context()

    async def _toggle_context(self):
        if not self.observer:
            return
        if self.observer.task and not self.observer.task.done():
            await self.observer.stop()
            self.emit("context.paused", context_enabled=False, context_status="屏幕总结已关闭", context_text="")
        else:
            self.emit("context.enabled", context_enabled=True)
            self.observer.task = asyncio.create_task(self.observer.run())

    def emit(self, kind, **changes):
        for key, value in changes.items():
            if not hasattr(self.state, key):
                raise ValueError(f"未知状态字段：{key}")
            setattr(self.state, key, value)
        self.state.revision += 1
        self.publish({"type": kind, "occurred_at": time.time(), "state": self.state.payload()})

    async def toggle_voice(self):
        async with self.voice_toggle_lock:
            await self._toggle_voice()

    async def _toggle_voice(self):
        if self.voice_task and not self.voice_task.done():
            self.emit("voice.stopping", voice="stopping", speaking=False, user_speaking=False)
            await self.stop_voice()
            return
        self.emit("voice.connecting", voice="connecting", error="")
        self.voice = self.voice_factory(self.handle_tool, self.emit)
        self.voice_task = asyncio.create_task(self.run_voice())

    async def run_voice(self):
        try:
            await self.voice.run()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.emit("voice.error", error=str(exc) or "语音连接失败")
        finally:
            self.emit("voice.off", voice="off", speaking=False, user_speaking=False)

    async def handle_tool(self, name, arguments):
        try:
            data = json.loads(arguments) if isinstance(arguments, str) else arguments
            if name == "run_task":
                accepted = self.start_task(data["goal"])
                if accepted["status"] != "accepted":
                    return accepted
                # 语音会话关闭不会误取消后台任务；窗口仍能收到结果。
                return await asyncio.shield(self.job)
            if name == "cancel_task":
                await self.cancel_task()
                return self.last_result
            if name == "task_status":
                return {**self.last_result, "progress": self.state.task_text}
            return {"status": "failed", "message": "未知的委托入口。"}
        except (ValueError, KeyError, TypeError, SyntaxError) as exc:
            return {"status": "failed", "message": str(exc)}

    def start_task(self, goal, *, from_text=False):
        """语音与文字共用同一入口；接收与执行分离，界面不等待任务结束。"""
        if not isinstance(goal, str) or not goal.strip():
            return {"status": "failed", "message": "请输入要完成的事情"}
        if self.job and not self.job.done():
            return {"status": "busy", "message": "请等待当前任务结束，或先停止任务"}
        task_id = uuid.uuid4().hex[:12]
        self.executor = self.executor_factory(task_id)
        changes = {"user_text": goal, "assistant_text": ""} if from_text else {}
        self.emit("task.accepted", task="accepted", task_id=task_id,
                  task_text="正在准备…", error="", task_started_at=time.time(), task_ended_at=0,
                  task_activity_at=time.time(), **changes)
        self.last_result = {"status": "running", "task_id": task_id, "goal": goal}
        self.job = asyncio.create_task(self.run_job(goal))
        return {"status": "accepted", "task_id": task_id}

    async def submit_text(self, goal):
        return self.start_task(goal, from_text=True)

    async def run_job(self, goal):
        self.emit("task.started", task="running")
        monitor = asyncio.create_task(self.monitor_job())
        try:
            result = await self.executor.run(goal, self.progress, self.approve)
            if self.state.task == "cancelling":
                raise asyncio.CancelledError
            status = "succeeded" if result["outcome"] == "completed" else result["outcome"]
            self.last_result = {"status": status, "task_id": self.state.task_id, "goal": goal, **result}
            self.emit("task." + status, task=status, task_text=result["summary"], approval="", task_ended_at=time.time())
        except asyncio.CancelledError:
            self.last_result = {"status": "cancelled", "message": "已停止后续操作；已发生的操作不会撤销。"}
            self.emit("task.cancelled", task="cancelled", task_text="任务已停止；已发生的操作不会撤销", approval="", task_ended_at=time.time())
        except Exception as exc:
            message = (str(exc) if isinstance(exc, TaskFailure) else
                       "执行超时，任务已结束，请稍后重试" if isinstance(exc, TimeoutError) else
                       "执行出现异常，任务已结束，请稍后重试")
            self.last_result = {"status": "failed", "task_id": self.state.task_id,
                                "message": message, "error_code": getattr(exc, "code", "execution_error")}
            self.emit("task.failed", task="failed", task_text=self.last_result["message"], approval="", task_ended_at=time.time())
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        return self.last_result

    async def monitor_job(self):
        while True:
            await asyncio.sleep(5)
            self.emit("task.heartbeat")

    def progress(self, text):
        if self.state.task != "cancelling":
            self.emit("task.progress", task_text=text, task_activity_at=time.time())

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
        self.emit("task.approval", task="awaiting_approval", approval=message)
        try:
            accepted = await asyncio.wait_for(self.approval_future, 55)
            if accepted:
                self.approved_requests.add(key)
            return accepted
        except TimeoutError:
            return False
        finally:
            self.approval_future = None
            self.emit("task.approval_resolved", approval="",
                      task="cancelling" if self.state.task == "cancelling" else "running")

    async def answer_approval(self, allowed):
        if self.approval_future and not self.approval_future.done():
            self.approval_future.set_result(allowed)

    async def cancel_task(self):
        if not self.job or self.job.done():
            return
        self.emit("task.cancelling", task="cancelling", task_text="正在停止后续操作…")
        await self.answer_approval(False)
        await self.executor.cancel()
        self.job.cancel()
        await asyncio.gather(self.job, return_exceptions=True)
        if self.state.task == "cancelling":
            self.last_result = {"status": "cancelled", "message": "任务已停止"}
            self.emit("task.cancelled", task="cancelled", task_text="任务已停止", approval="", task_ended_at=time.time())

    async def close(self):
        if self.observer:
            await self.observer.stop()
        await self.cancel_task()
        await self.stop_voice()

    async def stop_voice(self):
        if self.voice:
            with contextlib.suppress(Exception):
                await self.voice.stop()
        if self.voice_task:
            try:
                await asyncio.wait_for(asyncio.shield(self.voice_task), 5)
            except TimeoutError:
                self.voice_task.cancel()
                await asyncio.gather(self.voice_task, return_exceptions=True)
