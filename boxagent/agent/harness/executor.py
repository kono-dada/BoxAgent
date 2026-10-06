"""Harness execution lifecycle around one Agent Runtime request."""

import asyncio
import json
import time
import traceback
from pathlib import Path
from typing import Callable

from boxagent.core.errors import redact, timestamp, write_json
from boxagent.agent.harness.request import HarnessInput
from boxagent.agent.harness.policies.result import validate_task_result


class TaskExecutor:
    """Harness one AgentRuntime with one Computer Use session for a task."""

    def __init__(self, task_id: str, *, provider: str, model: str, output: Path,
                 runtime_factory: Callable, session_factory: Callable,
                 tool_gateway_factory: Callable, request_factory: Callable,
                 auto_approve: bool = True, session_id=None, interaction_id=None,
                 prior_messages=(), runtime_binding=None, on_thread_bound=None,
                 environment_provider=None, memory_provider=None):
        self.task_id = task_id
        self.provider = provider
        self.model = model
        self.output = Path(output)
        self.runtime_factory = runtime_factory
        self.session_factory = session_factory
        self.tool_gateway_factory = tool_gateway_factory
        self.request_factory = request_factory
        self.auto_approve = auto_approve
        self.session_id = session_id
        self.interaction_id = interaction_id
        self.prior_messages = tuple(prior_messages)
        self.runtime_binding = runtime_binding
        self.on_thread_bound = on_thread_bound
        self.environment_provider = environment_provider
        self.memory_provider = memory_provider
        self.session = None
        self.gateway = None
        self.runtime = None
        self.stopped = False
        self.phase = "connecting"
        self.started_at = time.time()
        self.last_activity_at = self.started_at
        self.goal = ""
        self.progress = lambda _message: None
        self.approve = None

    @property
    def process(self):
        return self.session.process if self.session else None

    @property
    def cleanup_status(self):
        return self.session.cleanup_status if self.session else "not_started"

    @property
    def actions(self):
        return self.gateway.actions if self.gateway else 0

    @property
    def tool_specs(self):
        return self.gateway.tool_specs if self.gateway else {}

    @property
    def observations(self):
        return self.gateway.observations if self.gateway else {}

    @property
    def tool_errors(self):
        return self.gateway.tool_errors if self.gateway else []

    @property
    def active_app(self):
        return self.gateway.active_app if self.gateway else ""

    def report(self, phase: str, message: str):
        self.phase = phase
        self.last_activity_at = time.time()
        self.progress(message)
        self.log("phase", phase=phase, message=message)

    def bind_tools(self, tools: list[dict]) -> list[dict]:
        return self.gateway.bind(tools)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        if self.gateway is None:
            raise RuntimeError("Computer Use 工具网关尚未启动")
        return await self.gateway.call(name, arguments)

    def validate_result(self, text: str) -> dict:
        return validate_task_result(text, self.observations, self.tool_errors)

    def capture_environment(self):
        if self.environment_provider is None:
            return None
        try:
            return self.environment_provider.capture()
        except Exception as exc:
            self.log("environment_capture_failed", error=str(exc))
            return None

    async def _execute(self, goal: str) -> dict:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("请提供要完成的目标")
        self.goal = goal
        self.output.mkdir(parents=True, exist_ok=True)
        session_arguments = dict(
            output=self.output,
            on_tool_call=self.call_tool,
            approve=self.approve,
            current_app=lambda: self.active_app,
            report=self.report,
            log=self.log,
            auto_approve=self.auto_approve,
        )
        if self.session_id:
            session_arguments.update(
                product_session_id=self.session_id,
                runtime_thread_id=(self.runtime_binding.thread_id
                                   if self.runtime_binding else None),
                on_thread_bound=self.on_thread_bound)
        self.session = self.session_factory(**session_arguments)
        self.gateway = self.tool_gateway_factory(
            self.session, output=self.output, goal=goal, report=self.report,
            log=self.log, is_stopped=lambda: self.stopped)
        try:
            raw_tools = await self.session.start()
            tools = self.bind_tools(raw_tools)
            self.log("tools_discovered", names=list(self.tool_specs))
            if self.stopped:
                raise asyncio.CancelledError
            self.runtime = self.runtime_factory(self.session)
            memories = ()
            if self.memory_provider is not None and self.session_id:
                try:
                    memories = tuple(await self.memory_provider.recall(
                        goal, session_id=self.session_id, top_k=5))
                except Exception as exc:
                    self.log("memory_recall_degraded", error=type(exc).__name__)
            request = self.request_factory(HarnessInput(
                goal=goal, turns=self.prior_messages, memories=memories,
                environment=self.capture_environment(),
                context_cursor=(self.runtime_binding.context_cursor
                                if self.runtime_binding else 0)))
            self.log("runtime_request_compiled",
                     persona=getattr(request, "persona_source", None),
                     input_characters=len(request.query),
                     instruction_characters=len(request.developer_instructions),
                     history_messages=len(request.history),
                     history_delta_messages=len(request.history_delta),
                     evidence_characters=len(request.evidence_context),
                     memory_evidence_count=len(memories))
            text = await asyncio.wait_for(self.runtime.execute(
                request, tools, self.call_tool, self.validate_result,
                lambda message: self.report("model", message)), 300)
            self.report("validating", "正在核对执行结果")
            write_json(self.output / "agent-result.json", {"text": text})
            return self.validate_result(text)
        finally:
            await self.session.close()

    async def run(self, goal, progress, approve):
        self.started_at = time.time()
        self.progress = progress
        self.approve = approve
        self.log("task_started", goal=goal, provider=self.provider, model=self.model,
                 auto_approve=self.auto_approve)
        write_json(self.output / "task.json", {"task_id": self.task_id, "goal": goal,
                                                "provider": self.provider, "model": self.model,
                                                "started_at": self.started_at})
        final = {"outcome": "failed"}
        heartbeat = asyncio.create_task(self.heartbeat())
        try:
            final = await self._execute(goal)
            return final
        except asyncio.CancelledError:
            final = {"outcome": "cancelled", "summary": "任务已停止"}
            self.log("task_cancelled")
            raise
        except Exception as exc:
            code = getattr(exc, "code", "timeout" if isinstance(exc, TimeoutError)
                           else "execution_error")
            final = {"outcome": "failed", "error_code": code,
                     "summary": str(exc) or "执行超时，任务已结束",
                     "detail": getattr(exc, "detail", ""), "tool_errors": self.tool_errors}
            self.log("task_failed", **final, exception_type=type(exc).__name__,
                     traceback=traceback.format_exc())
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.phase = "ended"
            process = self.process
            final.update(task_id=self.task_id, ended_at=time.time(),
                         elapsed=round(time.time() - self.started_at, 3), steps=self.actions,
                         runtime_thread_id=getattr(self.session, "thread_id", None),
                         runtime_process_alive=bool(
                             process is not None and process.returncode is None),
                         cursor_cleanup=self.cleanup_status)
            write_json(self.output / "result.json", final)
            self.log("task_ended", **{key: value for key, value in final.items()
                                     if key != "task_id"})
            self.write_status(False)

    async def cancel(self):
        self.log("cancel_requested", phase=self.phase, step=self.actions)
        self.stopped = True
        if self.session:
            await self.session.interrupt()

    def log(self, kind: str, **data):
        self.output.mkdir(parents=True, exist_ok=True)
        event = {"kind": kind, "occurred_at": time.time(), "timestamp": timestamp(),
                 "task_id": self.task_id,
                 "elapsed": round(time.time() - self.started_at, 3), **data}
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(redact(event), ensure_ascii=False) + "\n")

    def write_status(self, running: bool):
        process = self.process
        write_json(self.output / "status.json", {
            "task_id": self.task_id, "running": running, "phase": self.phase,
            "step": self.actions, "updated_at": time.time(),
            "last_activity_at": self.last_activity_at,
            "pid": process.pid if process else None,
            "process_returncode": process.returncode if process else None,
        })

    async def heartbeat(self):
        while True:
            self.write_status(True)
            process = self.process
            self.log("heartbeat", phase=self.phase, step=self.actions,
                     idle_seconds=round(time.time() - self.last_activity_at, 1),
                     pid=process.pid if process else None)
            await asyncio.sleep(10)
