"""Harness policy and gateway for model-issued Computer Use calls."""

import asyncio
import base64
import json
import traceback
from pathlib import Path

from boxagent.core.errors import redact


TOOL_LABELS = {
    "list_apps": "查找应用", "get_app_state": "观察界面", "click": "点击",
    "type_text": "输入", "press_key": "按键", "scroll": "滚动", "drag": "拖动",
    "set_value": "修改控件", "select_text": "选择文字",
    "perform_secondary_action": "操作控件",
}


def model_content(response: dict, step: int, goal: str = "") -> list[dict]:
    content = [{"type": "inputText", "text": f"观察步骤 {step}；以下是实际工具返回。"}]
    for item in response.get("content", []):
        if item.get("type") == "text":
            content.append({"type": "inputText", "text": item["text"]})
        elif item.get("type") == "image":
            content.append({"type": "inputImage",
                            "imageUrl": f"data:{item['mimeType']};base64,{item['data']}"})
    if goal:
        content.append({"type": "inputText", "text":
            "以上为工具观察数据。以下为 BoxAgent 保留的原始用户目标（未发生变更）：\n"
            + json.dumps(goal, ensure_ascii=False)
            + f"\n当前观察步骤为 {step}。继续完成此目标；若结束，依据观察汇报实际结果并引用步骤编号。"})
    return content


class ComputerUseToolGateway:
    def __init__(self, session, *, output: Path, goal: str, report, log, is_stopped):
        self.session = session
        self.output = Path(output)
        self.goal = goal
        self.report = report
        self.log = log
        self.is_stopped = is_stopped
        self.lock = asyncio.Lock()
        self.tool_specs = {}
        self.observations = {}
        self.tool_errors = []
        self.active_app = ""
        self.actions = 0

    def bind(self, tools):
        ordered = sorted(tools, key=lambda tool: tool["name"])
        self.tool_specs = {f"desktop_{tool['name']}": tool for tool in ordered}
        return [{"type": "function", "name": name,
                 "description": spec.get("description", ""),
                 "inputSchema": spec.get("inputSchema", {"type": "object", "properties": {}})}
                for name, spec in self.tool_specs.items()]

    async def call(self, name, arguments):
        step = None
        try:
            async with self.lock:
                if self.is_stopped():
                    raise RuntimeError("用户已取消，禁止继续执行动作")
                if name not in self.tool_specs:
                    raise ValueError("工具未在当前执行会话中注册")
                if not isinstance(arguments, dict):
                    raise ValueError("工具参数必须是对象")
                spec = self.tool_specs[name]
                schema = spec.get("inputSchema", {})
                if set(schema.get("required", [])) - arguments.keys():
                    raise ValueError("缺少工具必需的参数")
                allowed = schema.get("properties", {}).keys()
                if schema.get("additionalProperties") is False and arguments.keys() - allowed:
                    raise ValueError("工具参数不符合当前服务的协议")
                if self.actions >= 100:
                    raise RuntimeError("本次操作达到步数上限，请根据已有结果停止")
                operation = spec["name"]
                self.actions += 1
                step = self.actions
                self.active_app = arguments.get("app", "")
                self.report("tool", " · ".join(filter(None, [TOOL_LABELS.get(operation, operation),
                                                               self.active_app, f"第 {step} 步"])))
                self.log("action", step=step, operation=operation, arguments=arguments)
                response = await self.session.call_tool(operation, arguments)
                text = "\n".join(item["text"] for item in response.get("content", [])
                                 if item.get("type") == "text")
                failed = bool(response.get("isError", False))
                self.log("tool_result", step=step, operation=operation, text=text, error=failed)
                observation = {"step": step, "tool": operation, "app": self.active_app,
                               "success": not failed}
                self.observations[step] = observation
                (self.output / f"step-{step}.txt").write_text(redact(text), encoding="utf-8")
                if failed:
                    self.tool_errors.append({"step": step, "tool": operation, "error": text})
                else:
                    self._save_images(step, response, observation)
                self.report("model", "操作返回错误，正在判断下一步" if failed else "正在判断下一步")
                return {"success": not failed,
                        "contentItems": model_content(response, step, self.goal)}
        except (ValueError, KeyError, TypeError, RuntimeError, TimeoutError) as exc:
            if step is not None:
                self.observations[step] = {"step": step, "tool": name,
                                           "app": self.active_app, "success": False}
                self.tool_errors.append({"step": step, "tool": name,
                                         "error": str(exc) or "操作超时"})
            self.log("tool_exception", tool=name, arguments=arguments, error=repr(exc),
                     traceback=traceback.format_exc())
            return {"success": False, "contentItems": [
                {"type": "inputText", "text": str(exc) or "操作超时"}]}

    def _save_images(self, step, response, observation):
        for index, item in enumerate(response.get("content", [])):
            if item.get("type") != "image":
                continue
            suffix = "png" if item.get("mimeType") == "image/png" else "jpg"
            path = self.output / f"step-{step}-{index}.{suffix}"
            path.write_bytes(base64.b64decode(item["data"]))
            observation["screenshot"] = str(path)
