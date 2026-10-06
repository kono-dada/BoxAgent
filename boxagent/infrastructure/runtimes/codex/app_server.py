"""Codex App Server process and JSON-RPC session."""

import asyncio
import contextlib
import json
import os
import shutil
import time
import traceback
from pathlib import Path

from boxagent.agent.runtime.models import ModelProfile
from boxagent.infrastructure.runtimes.codex.computer_use import resolve_computer_use_client
from boxagent.infrastructure.runtimes.codex.skills import CodexSkillAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_codex_runtime(workspace=PROJECT_ROOT):
    workspace = Path(workspace)
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    candidates = []
    if configured := os.environ.get("BOXAGENT_CODEX_BIN"):
        candidates.append(Path(configured).expanduser())
    candidates.extend([
        workspace / ".runtime/codex-0.153.0/codex",
        codex_home / "plugins/.plugin-appserver/codex-cli/bin/codex",
    ])
    if executable := shutil.which("codex"):
        candidates.append(Path(executable))
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.is_file() and candidate.with_name("codex-code-mode-host").is_file():
            return candidate
    raise RuntimeError("找不到同时包含 codex 和 codex-code-mode-host 的运行时；"
                       "可通过 BOXAGENT_CODEX_BIN 指定")


class CodexAppServer:
    """Own one long-lived App Server process and one reusable Codex thread."""

    def __init__(self, *, output, on_tool_call, approve, current_app, report, log,
                 auto_approve=True, runtime_resolver=None,
                 workspace=PROJECT_ROOT, model_profile: ModelProfile | None = None,
                 permission_check=None, codex_home=None, skill_service=None):
        self.output = Path(output)
        self.on_tool_call = on_tool_call
        self.approve = approve
        self.current_app = current_app
        self.report = report
        self.log = log
        self.auto_approve = auto_approve
        self.workspace = Path(workspace)
        self.codex_home = Path(codex_home) if codex_home else None
        self.runtime_resolver = runtime_resolver or (
            lambda: resolve_codex_runtime(self.workspace))
        self.model_profile = model_profile
        self.permission_check = permission_check or self._default_permission_check
        self.skill_adapter = (CodexSkillAdapter(
            skill_service, workspace=self.workspace,
            log=lambda event, **data: self.log(event, **data))
            if skill_service else None)
        self.process = self.reader = self.stderr = self.client = None
        self.pending = {}
        self.callbacks = set()
        self.sequence = 0
        self.thread_id = self.turn_id = None
        self.thread_signature = None
        self.completed = None
        self.agent_text = ""
        self.tools = []
        self.discovered_tools = []
        self.discovered_skills = []
        self.skills_changed = False
        self.stopped = False
        self.cleanup_status = "not_started"

    @staticmethod
    def _default_permission_check(params, *, stopped=False):
        return (not stopped and params.get("serverName") == "boxagent_cua"
                and params.get("requestedSchema", {}).get("properties") == {})

    def bind_task(self, *, on_tool_call, approve, current_app, report, log,
                  auto_approve=True):
        """Route callbacks for the one task currently leasing this process."""
        self.on_tool_call = on_tool_call
        self.approve = approve
        self.current_app = current_app
        self.report = report
        self.log = log
        self.auto_approve = auto_approve
        self.stopped = False
        self.turn_id = None
        self.completed = None
        self.agent_text = ""
        self.cleanup_status = "not_started"

    def launch_environment(self) -> dict[str, str]:
        env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "CODEX_HOME")
               if key in os.environ}
        profile = self.model_profile
        if self.codex_home:
            self.codex_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(self.codex_home)
        if profile and profile.api_key_env and profile.api_key:
            env[profile.api_key_env] = profile.api_key
        return env

    def model_config_overrides(self) -> list[str]:
        profile = self.model_profile
        if profile is None:
            return []
        overrides = [
            "model=" + json.dumps(profile.model),
            "model_provider=" + json.dumps(profile.provider),
        ]
        if not profile.is_custom_provider:
            return overrides
        if not profile.base_url or not profile.api_key_env or not profile.model_catalog:
            raise RuntimeError(f"模型供应商 {profile.provider} 的配置不完整")
        if not profile.api_key:
            raise RuntimeError(f"未配置 {profile.api_key_env}，无法启动 {profile.display_name}")
        if not profile.model_catalog.is_file():
            raise RuntimeError(f"模型目录不存在：{profile.model_catalog}")
        prefix = f"model_providers.{profile.provider}"
        overrides.extend([
            f"{prefix}.name=" + json.dumps(profile.display_name),
            f"{prefix}.base_url=" + json.dumps(profile.base_url),
            f"{prefix}.env_key=" + json.dumps(profile.api_key_env),
            f"{prefix}.wire_api=\"responses\"",
            "model_catalog_json=" + json.dumps(str(profile.model_catalog)),
            "model_reasoning_effort=\"high\"",
            "web_search=\"disabled\"",
        ])
        return overrides

    def launch_arguments(self, executable: Path, client: Path) -> list[str]:
        mcp = 'mcp_servers.boxagent_cua={command=' + json.dumps(str(client)) + ',args=["mcp"]}'
        arguments = [str(executable), "app-server", "--stdio", "-c", mcp, "-c", "notify=[]"]
        for override in self.model_config_overrides():
            arguments.extend(("-c", override))
        return arguments

    async def send(self, message):
        if not self.process or self.process.returncode is not None:
            raise RuntimeError("任务执行器已断开")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def rpc(self, method, params, timeout=120):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        started = time.monotonic()
        self.log("rpc_started", request_id=request_id, method=method, timeout=timeout)
        try:
            await self.send({"id": request_id, "method": method, "params": params})
            result = await asyncio.wait_for(future, timeout)
            self.log("rpc_finished", request_id=request_id, method=method,
                     duration=round(time.monotonic() - started, 3))
            return result
        except BaseException as exc:
            self.log("rpc_failed", request_id=request_id, method=method, error=repr(exc),
                     duration=round(time.monotonic() - started, 3))
            raise
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        error = RuntimeError("Codex 执行器连接已结束")
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                method = message.get("method")
                if method in {"error", "turn/completed", "item/started", "item/completed"}:
                    params = message.get("params", {})
                    item = params.get("item", {})
                    self.log("server_event", method=method, item_type=item.get("type"),
                             item_id=item.get("id"),
                             turn={key: params.get("turn", {}).get(key)
                                   for key in ("id", "status", "error")}
                             if method == "turn/completed" else None,
                             error=params.get("error"))
                elif method in {"thread/tokenUsage/updated", "thread/compacted"}:
                    self.log("runtime_context", method=method, params=message.get("params", {}))
                elif method == "skills/changed":
                    self.skills_changed = True
                    self.log("skills_changed")
                if method and "id" in message:
                    task = asyncio.create_task(self.callback(message))
                    self.callbacks.add(task)
                    task.add_done_callback(self.callbacks.discard)
                elif (future := self.pending.get(message.get("id"))) is not None:
                    if not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(str(message["error"])))
                        else:
                            future.set_result(message.get("result", {}))
                elif method == "turn/completed" and self.completed and not self.completed.done():
                    turn = message["params"]["turn"]
                    if turn.get("id") == self.turn_id:
                        self.completed.set_result(turn)
                elif method == "item/completed":
                    params = message.get("params", {})
                    item = params.get("item", {})
                    if params.get("turnId") == self.turn_id and item.get("type") == "agentMessage":
                        self.agent_text = item.get("text", "")
                        self.log("agent_message", text=self.agent_text)
        except Exception as exc:
            error = exc
            self.log("reader_failed", error=repr(exc), traceback=traceback.format_exc())
        finally:
            futures = list(self.pending.values())
            if self.completed is not None:
                futures.append(self.completed)
            for future in futures:
                if not future.done():
                    future.set_exception(error)

    async def callback(self, message):
        method, params = message["method"], message.get("params", {})
        try:
            if method == "item/tool/call":
                result = await self.on_tool_call(params.get("tool"), params.get("arguments", {}))
            elif method == "mcpServer/elicitation/request":
                app = self.current_app()
                self.log("approval_requested", app=app, message=params.get("message"))
                accepted = self.permission_check(params, stopped=self.stopped)
                if accepted and not self.auto_approve:
                    self.report("approval", "等待你的授权")
                    accepted = await self.approve({**params, "app": app})
                accepted = accepted and not self.stopped
                result = {"action": "accept", "content": {}} if accepted else {"action": "decline"}
                self.log("approval_resolved", accepted=bool(accepted), automatic=self.auto_approve)
            else:
                await self.send({"id": message["id"], "error": {
                    "code": -32601, "message": "暂不支持此宿主回调"}})
                return
            await self.send({"id": message["id"], "result": result})
        except Exception as exc:
            self.log("callback_failed", method=method, error=repr(exc),
                     traceback=traceback.format_exc())
            with contextlib.suppress(Exception):
                await self.send({"id": message["id"], "error": {"code": -32603, "message": str(exc)}})

    async def discover_tools(self):
        bootstrap = await self.rpc("thread/start", {"cwd": str(self.workspace), "ephemeral": True,
                                                   "approvalPolicy": "on-request", "sandbox": "read-only"})
        self.log("tool_discovery_thread_started",
                 thread_id=bootstrap.get("thread", {}).get("id"))
        async with asyncio.timeout(30):
            while True:
                inventory = await self.rpc("mcpServerStatus/list", {})
                server = next((item for item in inventory.get("data", [])
                               if item["name"] == "boxagent_cua"), None)
                if server and server.get("tools"):
                    tools = server["tools"]
                    return list(tools.values()) if isinstance(tools, dict) else tools
                if self.stopped:
                    raise asyncio.CancelledError
                await asyncio.sleep(.2)

    async def call_tool(self, operation, arguments):
        metadata = {"codex.session_id": self.thread_id}
        if self.turn_id:
            metadata["codex.turn_id"] = self.turn_id
        return await self.rpc("mcpServer/tool/call", {
            "threadId": self.thread_id, "server": "boxagent_cua", "tool": operation,
            "_meta": metadata, "arguments": arguments})

    async def ensure_thread(self, *, model, instructions, preferred_thread_id=None,
                            product_session_id=None):
        await self.refresh_skills(force=self.skills_changed)
        skill_signature = self.skill_adapter.signature if self.skill_adapter else ()
        signature = (model, instructions,
                     json.dumps(self.tools, sort_keys=True, ensure_ascii=False),
                     skill_signature)
        same_bound_thread = preferred_thread_id and self.thread_id == preferred_thread_id
        standalone_reuse = preferred_thread_id is None and product_session_id is None
        if self.thread_id and (same_bound_thread or standalone_reuse) \
                and self.thread_signature == signature:
            self.log("runtime_epoch_reused", thread_id=self.thread_id, model=model)
            return self.thread_id, "reused"
        configuration_changed = (
            self.thread_id == preferred_thread_id
            and self.thread_signature is not None
            and self.thread_signature != signature)
        if preferred_thread_id and not configuration_changed:
            try:
                resumed = await self.rpc("thread/resume", {
                    "threadId": preferred_thread_id,
                    "cwd": str(self.workspace),
                    "approvalPolicy": "on-request", "sandbox": "read-only",
                    "model": model, "developerInstructions": instructions,
                    "dynamicTools": self.tools,
                })
                self.thread_id = resumed["thread"]["id"]
                self.thread_signature = signature
                self.log("runtime_thread_resumed", thread_id=self.thread_id,
                         model=model, product_session_id=product_session_id)
                return self.thread_id, "resumed"
            except Exception as exc:
                self.log("runtime_thread_resume_failed", thread_id=preferred_thread_id,
                         model=model, product_session_id=product_session_id,
                         error=repr(exc))
        thread = await self.rpc("thread/start", {
            "cwd": str(self.workspace), "ephemeral": False,
            "approvalPolicy": "on-request", "sandbox": "read-only", "model": model,
            "developerInstructions": instructions, "dynamicTools": self.tools,
        })
        self.thread_id = thread["thread"]["id"]
        self.thread_signature = signature
        self.log("runtime_epoch_started", thread_id=self.thread_id, model=model,
                 product_session_id=product_session_id)
        return self.thread_id, "started"

    async def run_codex_turn(self, *, model, instructions, query, output_schema,
                             history=(), history_delta=(), evidence_context="",
                             preferred_thread_id=None,
                             on_thread_bound=None, product_session_id=None):
        self.completed = asyncio.get_running_loop().create_future()
        self.agent_text = ""
        thread_id, state = await self.ensure_thread(
            model=model, instructions=instructions,
            preferred_thread_id=preferred_thread_id,
            product_session_id=product_session_id)
        if on_thread_bound:
            result = on_thread_bound(thread_id)
            if asyncio.iscoroutine(result):
                await result
        if self.stopped:
            raise asyncio.CancelledError
        selected_history = history if state == "started" else history_delta
        if selected_history:
            items = []
            for message in selected_history:
                if message.role not in {"user", "assistant"}:
                    raise ValueError("Codex 历史消息只支持 user/assistant role")
                items.append({
                    "type": "message",
                    "role": message.role,
                    "content": [{
                        "type": ("input_text" if message.role == "user"
                                 else "output_text"),
                        "text": message.content,
                    }],
                })
            await self.rpc("thread/inject_items", {
                "threadId": self.thread_id,
                "items": items,
            })
            self.log(
                "runtime_history_injected", thread_id=self.thread_id,
                message_count=len(items),
                first_sequence=selected_history[0].sequence,
                last_sequence=selected_history[-1].sequence,
                thread_state=state)
        turn_input = query
        if evidence_context:
            turn_input = evidence_context + "\n当前用户请求：\n" + query
        turn = await self.rpc("turn/start", {"threadId": self.thread_id,
            "outputSchema": output_schema,
            "input": [{"type": "text", "text": turn_input, "text_elements": []}]})
        self.turn_id = turn["turn"]["id"]
        self.log("turn_started", thread_id=self.thread_id, turn_id=self.turn_id)
        result = await asyncio.wait_for(asyncio.shield(self.completed), 300)
        if self.stopped or result.get("status") == "interrupted":
            raise asyncio.CancelledError
        if result.get("status") != "completed":
            raise RuntimeError("后台执行未结束：" + str(result.get("error") or result.get("status")))
        return self.agent_text

    async def run_isolated_structured_turn(self, *, model, instructions, query,
                                            output_schema):
        """Run a tool-free ephemeral turn without changing the task Thread."""
        self.completed = asyncio.get_running_loop().create_future()
        self.agent_text = ""
        thread = await self.rpc("thread/start", {
            "cwd": str(self.workspace),
            "ephemeral": True,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "model": model,
            "developerInstructions": instructions,
            "dynamicTools": [],
        })
        isolated_thread_id = thread["thread"]["id"]
        turn = await self.rpc("turn/start", {
            "threadId": isolated_thread_id,
            "outputSchema": output_schema,
            "input": [{"type": "text", "text": query, "text_elements": []}],
        })
        self.turn_id = turn["turn"]["id"]
        self.log("structured_turn_started", thread_id=isolated_thread_id,
                 turn_id=self.turn_id)
        try:
            result = await asyncio.wait_for(asyncio.shield(self.completed), 180)
            if result.get("status") != "completed":
                raise RuntimeError(
                    "结构化请求未完成：" + str(result.get("error") or result.get("status")))
            return self.agent_text
        finally:
            self.turn_id = None
            self.completed = None
            self.cleanup_status = "not_needed"

    async def start(self):
        if self.process and self.process.returncode is None:
            await self.refresh_skills()
            return self.discovered_tools
        executable = self.runtime_resolver()
        self.client = resolve_computer_use_client()
        arguments = self.launch_arguments(executable, self.client)
        env = self.launch_environment()
        self.output.mkdir(parents=True, exist_ok=True)
        self.stderr = (self.output / "codex.log").open("w")
        self.report("connecting", "正在连接执行器")
        self.process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=self.workspace, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=self.stderr, limit=64 * 1024 * 1024)
        self.log("process_started", pid=self.process.pid)
        if self.stopped:
            raise asyncio.CancelledError
        self.reader = asyncio.create_task(self.read())
        await self.rpc("initialize", {"clientInfo": {"name": "boxagent-pet", "version": "0.4.0"},
                                      "capabilities": {"experimentalApi": True}})
        await self.send({"method": "initialized"})
        await self.refresh_skills(force=True)
        self.discovered_tools = await self.discover_tools()
        self.log("tools_discovered", count=len(self.discovered_tools))
        return self.discovered_tools

    async def refresh_skills(self, *, force=False):
        if self.skill_adapter is None:
            return self.discovered_skills
        self.discovered_skills = await self.skill_adapter.sync(
            self.rpc, force=force)
        self.skills_changed = False
        return self.discovered_skills

    async def end_turn(self):
        if not self.thread_id or not self.turn_id:
            self.cleanup_status = "not_needed"
            return
        payload = {"type": "agent-turn-complete", "thread-id": self.thread_id,
                   "turn-id": self.turn_id, "cwd": str(self.workspace), "input-messages": [],
                   "last-assistant-message": ""}
        process = None
        self.log("cursor_cleanup_started", thread_id=self.thread_id, turn_id=self.turn_id)
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.client), "turn-ended", json.dumps(payload),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
            self.cleanup_status = "notified" if process.returncode == 0 else "failed"
            self.log("cursor_cleanup_finished", status=self.cleanup_status,
                     returncode=process.returncode, stdout=stdout.decode(errors="replace"),
                     stderr=stderr.decode(errors="replace"))
        except Exception as exc:
            self.cleanup_status = "failed"
            self.log("cursor_cleanup_failed", error=repr(exc))
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            self.turn_id = None

    async def interrupt(self):
        self.stopped = True
        if self.thread_id and self.turn_id and self.process and self.process.returncode is None:
            with contextlib.suppress(Exception):
                await self.rpc("turn/interrupt", {"threadId": self.thread_id,
                                                  "turnId": self.turn_id}, timeout=3)

    async def release_task(self):
        self.report("cleanup", "正在结束执行会话")
        for task in list(self.callbacks):
            task.cancel()
        await asyncio.gather(*self.callbacks, return_exceptions=True)
        await self.end_turn()
        if self.completed is not None and self.completed.done() and not self.completed.cancelled():
            self.completed.exception()
        self.completed = None
        self.agent_text = ""

    async def close(self):
        self.stopped = True
        await self.release_task()
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.stderr:
            self.stderr.close()
        self.log("process_exited", pid=self.process.pid if self.process else None,
                 returncode=self.process.returncode if self.process else None)
