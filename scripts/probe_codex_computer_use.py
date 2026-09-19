# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""由 Python 启动 Codex App Server，通过其工具接口读取计算器。"""

import argparse
import asyncio
import base64
import contextlib
import json
import os
import re
from pathlib import Path
import shutil
import sys
import time


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-calculator-once", action="store_true")
    parser.add_argument("--agent", action="store_true", help="通过 Codex 模型发起工具调用，会产生模型用量")
    parser.add_argument("--codex-bin", help="使用指定的本机 Codex 可执行文件")
    parser.add_argument("--capture-handshake", action="store_true", help="仅向诊断服务发送初始化消息，不连接真实执行器")
    parser.add_argument("--calculate", action="store_true", help="操作计算器计算 137+248，并从界面核验 385")
    parser.add_argument("--dynamic-tool", action="store_true", help="向模型显式提供工具，Python 将调用转交同一个 App Server")
    parser.add_argument("--model", help="模型实验使用的 Codex 模型；省略时继承配置")
    parser.add_argument("--disable-code-mode-host", action="store_true", help="仅用于对照模型工具运行环境")
    args = parser.parse_args()
    if not args.allow_calculator_once:
        parser.error("读取计算器需要 --allow-calculator-once")
    if args.capture_handshake and (args.agent or args.calculate):
        parser.error("握手诊断不能与模型或计算操作混用")
    if args.dynamic_tool and not args.agent:
        parser.error("--dynamic-tool 需要 --agent")
    pinned = Path(__file__).resolve().parents[1] / ".runtime/codex-0.153.0/codex"
    executable = shutil.which(args.codex_bin or (str(pinned) if pinned.is_file() else "codex"))
    if not executable:
        raise RuntimeError("PATH 中没有 codex")
    if args.agent and not args.disable_code_mode_host:
        host = Path(executable).resolve().with_name("codex-code-mode-host")
        if not host.is_file():
            raise RuntimeError(f"模型工具调用缺少配套运行时：{host}；请安装同版本官方 codex-code-mode-host")
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    client = codex_home / "computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient"
    if not client.is_file():
        raise RuntimeError(f"找不到执行器：{client}")
    output = Path(__file__).resolve().parents[1] / ".runtime/private/artifacts/computer-use/python-app-server" / time.strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    started = time.monotonic()
    events = []

    def log(stage, **data):
        record = {"elapsedMs": round((time.monotonic() - started) * 1000), "stage": stage, **data}
        # 截图原文保存在 result.json，终端与事件日志只保留大小。
        def summarize(value):
            if isinstance(value, dict):
                if value.get("type") == "image" and "data" in value:
                    return {"type": "image", "mimeType": value.get("mimeType"), "encodedLength": len(value["data"])}
                return {key: summarize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [summarize(item) for item in value]
            return value
        record = summarize(record)
        events.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    config = 'mcp_servers.boxagent_cua=' + '{command=' + json.dumps(str(client)) + ',args=["mcp"],enabled_tools=["get_app_state"]}'
    if args.calculate:
        config = config.replace('["get_app_state"]', '["get_app_state","click","type_text","press_key"]')
    if args.capture_handshake:
        capture = Path(__file__).with_name("capture_mcp_initialize.py")
        config = 'mcp_servers.boxagent_cua={command=' + json.dumps(sys.executable) + ',args=[' + json.dumps(str(capture)) + ',' + json.dumps(str(output / "initialize.jsonl")) + ']}'
    env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR", "LANG", "CODEX_HOME") if key in os.environ}
    pending = {}
    completed = asyncio.get_running_loop().create_future()
    tool_items = []
    backend_reads = []
    callbacks = set()
    sequence = 0
    stderr = (output / "stderr.log").open("w")
    process = await asyncio.create_subprocess_exec(
        executable, "app-server", "--stdio", *(["--disable", "code_mode_host"] if args.disable_code_mode_host else []), "-c", config,
        env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=stderr, limit=64 * 1024 * 1024,
    )

    async def send(message):
        process.stdin.write((json.dumps(message) + "\n").encode())
        await process.stdin.drain()

    async def read():
        try:
            while line := await process.stdout.readline():
                message = json.loads(line)
                if "method" in message and "id" in message:
                    params = message.get("params", {})
                    if args.dynamic_tool and message["method"] == "item/tool/call" and params.get("tool") == "calculator_ui":
                        # 回调中还需向 App Server 发请求，不能阻塞读取响应的循环。
                        task = asyncio.create_task(handle_dynamic(message))
                        callbacks.add(task)
                        task.add_done_callback(callbacks.discard)
                        continue
                    allowed = (message["method"] == "mcpServer/elicitation/request"
                               and params.get("serverName") == "boxagent_cua"
                               and params.get("message") == "Allow ChatGPT to use Calculator?")
                    log("approval", method=message["method"], accepted=allowed)
                    if message["method"] == "mcpServer/elicitation/request":
                        await send({"id": message["id"], "result": {"action": "accept" if allowed else "decline", "content": {} if allowed else None}})
                    else:
                        await send({"id": message["id"], "error": {"code": -32601, "message": "本实验不处理此回调"}})
                elif message.get("id") in pending:
                    future = pending[message["id"]]
                    if not future.done():
                        if "error" in message:
                            future.set_exception(RuntimeError(json.dumps(message["error"], ensure_ascii=False)))
                        else:
                            future.set_result(message.get("result"))
                elif message.get("method") == "item/completed":
                    item = message.get("params", {}).get("item", {})
                    tool_items.append(item)
                    log("item", item=item)
                elif message.get("method") == "turn/completed" and not completed.done():
                    completed.set_result(message["params"])
                elif message.get("method") == "mcpServer/startupStatus/updated":
                    params = message.get("params", {})
                    if params.get("name") == "boxagent_cua":
                        log("startup", **params)
        finally:
            for future in pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("App Server 已断开"))

    async def handle_dynamic(message):
        try:
            arguments = dict(message["params"]["arguments"])
            operation = arguments.pop("operation")
            permitted = {"get_app_state", "click", "type_text", "press_key"} if args.calculate else {"get_app_state"}
            if operation not in permitted or set(arguments) - {"element_index", "text", "key"}:
                raise RuntimeError("工具操作超出本次计算器范围")
            log("backend_call", operation=operation, arguments=arguments)
            response = await rpc("mcpServer/tool/call", {"threadId": thread_id, "server": "boxagent_cua", "tool": operation, "arguments": {"app": "com.apple.calculator", **arguments}})
            log("backend_result", operation=operation, result=response)
            if operation == "get_app_state" and not response.get("isError"):
                backend_reads.append(response)
            content = [{"type": "inputText", "text": c["text"]} for c in response.get("content", []) if c.get("type") == "text"]
            await send({"id": message["id"], "result": {"success": not response.get("isError", False), "contentItems": content}})
        except (RuntimeError, KeyError, TypeError, TimeoutError) as error:
            await send({"id": message["id"], "result": {"success": False, "contentItems": [{"type": "inputText", "text": str(error)}]}})

    async def rpc(method, params):
        nonlocal sequence
        sequence += 1
        request_id = sequence
        future = asyncio.get_running_loop().create_future()
        pending[request_id] = future
        try:
            await send({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, 45)
        finally:
            pending.pop(request_id, None)

    reader = asyncio.create_task(read())
    passed = False
    try:
        log("initialize", diagnostic=args.capture_handshake, result=await rpc("initialize", {"clientInfo": {"name": "boxagent-python-probe", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}}))
        await send({"method": "initialized"})
        scope = "读取并操作 com.apple.calculator，清除当前输入后计算 137+248，重新读取界面核验结果" if args.calculate else "读取 com.apple.calculator"
        tool_name = "calculator_ui 动态工具" if args.dynamic_tool else "boxagent_cua"
        dynamic = {"dynamicTools": [{"type": "function", "name": "calculator_ui", "description": "读取或操作真实计算器。先读取界面，点击索引必须来自最新界面。每组操作后读取核验。", "inputSchema": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["get_app_state", "click", "type_text", "press_key"] if args.calculate else ["get_app_state"]}, "element_index": {"type": "string"}, "text": {"type": "string"}, "key": {"type": "string"}}, "required": ["operation"], "additionalProperties": False}}]} if args.dynamic_tool else {}
        thread = await rpc("thread/start", {"cwd": str(Path.cwd()), "ephemeral": True, "approvalPolicy": "on-request", "sandbox": "read-only",
            "developerInstructions": f"本次仅验证 {tool_name}。只允许{scope}。禁止 shell、其他应用、其他工具或子代理。工具不可用时报告错误并停止。用户已授权本次计算器操作。", **dynamic, **({"model": args.model} if args.model else {})})
        thread_id = thread["thread"]["id"]
        log("thread", threadId=thread_id, model=thread.get("model"), codexExecutable=executable)
        inventory = await rpc("mcpServerStatus/list", {})
        server = next((s for s in inventory.get("data", []) if s["name"] == "boxagent_cua"), None)
        log("inventory", server=server)
        # 使用 App Server 提供的直接调用接口，不借用其他进程，也不调用模型。
        if args.agent:
            turn = await rpc("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": f"使用 {tool_name} {scope}，报告真实界面显示的数字。不要心算、不要执行任何命令；工具不可用时如实停止。", "text_elements": []}]})
            try:
                result = await asyncio.wait_for(asyncio.shield(completed), 90)
            except TimeoutError:
                await rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn["turn"]["id"]})
                raise
        else:
            async def call(tool, **arguments):
                response = await rpc("mcpServer/tool/call", {"threadId": thread_id, "server": "boxagent_cua", "tool": tool, "arguments": {"app": "com.apple.calculator", **arguments}})
                if response.get("isError"):
                    raise RuntimeError(json.dumps(response, ensure_ascii=False))
                return response
            result = await call("get_app_state")
            if args.calculate:
                state_text = "\n".join(c.get("text", "") for c in result["content"])
                match = re.search(r"^\s*(\d+) 按钮 Description: (?:全部清除|清除),", state_text, re.MULTILINE)
                if not match:
                    raise RuntimeError("当前界面未找到清除按钮，停止")
                await call("click", element_index=match[1])
                await call("get_app_state")
                await call("type_text", text="137+248")
                await call("get_app_state")
                await call("press_key", key="Return")
                result = await call("get_app_state")
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        log("tool_result", result=result)
        passed = not result.get("isError", False) and any(item.get("type") == "image" for item in result.get("content", []))
        if args.agent:
            passed = any(item.get("type") == "mcpToolCall" and item.get("server") == "boxagent_cua"
                         and item.get("tool") == "get_app_state" and item.get("status") == "completed"
                         and any(c.get("type") == "image" for c in (item.get("result") or {}).get("content", []))
                         for item in tool_items)
        read_results = [(item.get("result") or {}) for item in tool_items if item.get("type") == "mcpToolCall" and item.get("tool") == "get_app_state"] if args.agent else [result]
        if args.dynamic_tool:
            read_results = backend_reads
            passed = result.get("turn", {}).get("status") == "completed" and bool(backend_reads) and any(c.get("type") == "image" for c in backend_reads[-1].get("content", []))
            (output / "backend-reads.json").write_text(json.dumps(backend_reads, ensure_ascii=False))
        if read_results:
            last_read = read_results[-1]
            state_text = "\n".join(c.get("text", "") for c in last_read.get("content", []))
            (output / "state.txt").write_text(state_text)
            for item in last_read.get("content", []):
                if item.get("type") == "image" and item.get("data"):
                    suffix = "png" if item.get("mimeType") == "image/png" else "jpg"
                    (output / f"state.{suffix}").write_bytes(base64.b64decode(item["data"]))
            if args.calculate:
                normalized = re.sub(r"[\u200e\u200f\u2066-\u2069]", "", state_text)
                passed = passed and bool(re.search(r"^\s*\d+ 文本 385\s*$", normalized, re.MULTILINE))
        elif args.calculate:
            passed = False
    except (RuntimeError, TimeoutError) as error:
        log("error", message=str(error) or "请求超时")
    finally:
        for task in list(callbacks):
            task.cancel()
        if callbacks:
            await asyncio.gather(*callbacks, return_exceptions=True)
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                process.kill()
                await process.wait()
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        stderr.close()
        log("finished", passed=passed, evidence=str(output))
        (output / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
