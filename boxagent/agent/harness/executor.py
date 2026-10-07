"""Harness execution lifecycle around one Agent Runtime request."""

import asyncio
from dataclasses import asdict, is_dataclass
import hashlib
import inspect
import json
import time
import traceback
from pathlib import Path
from typing import Callable

from boxagent.core.errors import append_jsonl, redact, timestamp, write_json
from boxagent.agent.harness.request import HarnessInput
from boxagent.agent.harness.policies.result import validate_task_result


class TaskExecutor:
    """Harness one AgentRuntime with one Computer Use session for a task."""

    def __init__(self, task_id: str, *, provider: str, model: str, output: Path,
                 runtime_factory: Callable, session_factory: Callable,
                 tool_gateway_factory: Callable, request_factory: Callable,
                 auto_approve: bool = True, session_id=None, interaction_id=None,
                 prior_messages=(), runtime_binding=None, on_thread_bound=None,
                 environment_provider=None, memory=None,
                 run_index_path=None, latest_run_path=None):
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
        self.memory = memory
        self.run_index_path = Path(run_index_path) if run_index_path else None
        self.latest_run_path = Path(latest_run_path) if latest_run_path else None
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
            memory_retrieval = {
                "query": goal, "session_id": self.session_id or "",
                "consumer": "codex", "mode": "direct", "top_k": 5,
                "elapsed_ms": 0, "profile": [], "evidence": [],
                "narrative": [], "trace": {"degraded": True,
                                             "reason": "not_requested"},
            }
            if self.memory is not None and self.session_id:
                self.log("memory_recall_started", query=goal, mode="direct", top_k=5)
                recall_started = time.perf_counter()
                try:
                    detailed = getattr(self.memory, "evidence_with_trace", None)
                    if detailed is not None and inspect.iscoroutinefunction(detailed):
                        bundle = await detailed(
                            goal, session_id=self.session_id, top_k=5)
                        memories = tuple(bundle.get("items") or ())
                        memory_retrieval = dict(bundle.get("retrieval") or {})
                    else:
                        memories = tuple(await self.memory.evidence(
                            goal, session_id=self.session_id, top_k=5))
                        memory_retrieval = {
                            **memory_retrieval, "trace": {"degraded": False},
                            "evidence": list(memories),
                        }
                except Exception as exc:
                    memory_retrieval = {
                        **memory_retrieval,
                        "trace": {"degraded": True,
                                  "reason": type(exc).__name__},
                    }
                    self.log("memory_recall_degraded", error=type(exc).__name__)
                finally:
                    memory_retrieval["elapsed_ms"] = round(
                        (time.perf_counter() - recall_started) * 1000, 3)
            memory_retrieval["injected_items"] = list(memories)
            write_json(self.output / "memory-retrieval.json", memory_retrieval)
            self.log(
                "memory_recall_finished",
                elapsed_ms=memory_retrieval["elapsed_ms"], mode="direct",
                top_k=5, profile_count=len(memory_retrieval.get("profile", [])),
                evidence_count=len(memory_retrieval.get("evidence", [])),
                narrative_count=len(memory_retrieval.get("narrative", [])),
                injected_count=len(memories),
                injected_characters=len(json.dumps(
                    list(memories), ensure_ascii=False)),
                degraded=bool((memory_retrieval.get("trace") or {}).get(
                    "degraded")))
            environment = self.capture_environment()
            request = self.request_factory(HarnessInput(
                goal=goal, turns=self.prior_messages, memories=memories,
                environment=environment,
                context_cursor=(self.runtime_binding.context_cursor
                                if self.runtime_binding else 0)))
            history = [self._record(item) for item in request.history]
            history_delta = [self._record(item) for item in request.history_delta]
            write_json(self.output / "context.json", {
                "session_id": self.session_id,
                "interaction_id": self.interaction_id,
                "context_cursor": (self.runtime_binding.context_cursor
                                   if self.runtime_binding else 0),
                "current_user_query": goal,
                "environment": self._record(environment),
                "history": history,
                "history_delta": history_delta,
                "memory_evidence": list(memories),
                "assembled_evidence_context": request.evidence_context,
            })
            request_snapshot = {
                "provider": self.provider,
                "model": self.model,
                "session_id": self.session_id,
                "interaction_id": self.interaction_id,
                "runtime_thread_id": getattr(self.session, "thread_id", None),
                "query": request.query,
                "developer_instructions": request.developer_instructions,
                "developer_instructions_sha256": hashlib.sha256(
                    request.developer_instructions.encode("utf-8")).hexdigest(),
                "history": history,
                "history_delta": history_delta,
                "evidence_context": request.evidence_context,
                "output_schema": request.output_schema,
                "tools": tools,
            }
            write_json(self.output / "request.json", request_snapshot)
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
            request_snapshot["runtime_thread_id"] = getattr(
                self.session, "thread_id", None)
            request_snapshot["runtime_turn_id"] = getattr(
                self.session, "turn_id", None)
            write_json(self.output / "request.json", request_snapshot)
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
        manifest = {"task_id": self.task_id, "goal": goal,
                    "goal_preview": goal.strip().replace("\n", " ")[:80],
                    "provider": self.provider, "model": self.model,
                    "session_id": self.session_id,
                    "interaction_id": self.interaction_id,
                    "started_at": self.started_at,
                    "started_at_iso": timestamp(),
                    "run_directory": str(self.output)}
        write_json(self.output / "manifest.json", manifest)
        write_json(self.output / "task.json", manifest)
        self._index_run("started", manifest)
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
            self._index_run("ended", {**manifest, **final})
            self.write_status(False)

    @staticmethod
    def _record(value):
        if value is None:
            return None
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, dict):
            return dict(value)
        return {key: getattr(value, key) for key in (
            "role", "content", "sequence", "event_id", "source")
                if hasattr(value, key)}

    def _index_run(self, event, payload):
        value = {"event": event, "occurred_at": time.time(), **payload}
        if self.run_index_path is not None:
            append_jsonl(self.run_index_path, value)
        if self.latest_run_path is not None:
            write_json(self.latest_run_path, value)
            # Finder-friendly pointer; latest.json remains the machine-readable
            # source for tools and diagnostics.
            latest_link = self.latest_run_path.parent / "latest-run"
            temporary_link = self.latest_run_path.parent / (
                f".latest-run-{self.task_id}")
            try:
                temporary_link.unlink(missing_ok=True)
                temporary_link.symlink_to(
                    self.output.resolve(), target_is_directory=True)
                temporary_link.replace(latest_link)
            except OSError:
                temporary_link.unlink(missing_ok=True)

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
