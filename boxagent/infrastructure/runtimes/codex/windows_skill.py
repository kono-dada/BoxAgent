"""Load the installed Windows Computer Use skill and its registered Node MCP.

The desktop application owns @oai/sky and its native service. Reuse the
registered node_repl server instead of locating or starting a native helper.
"""

from dataclasses import dataclass
import json
import os
import re
from pathlib import Path
import tomllib
from typing import Callable, Mapping

from boxagent.core.errors import TaskFailure


@dataclass(frozen=True)
class WindowsSkill:
    command: Path
    arguments: tuple[str, ...]
    environment: Mapping[str, str]
    inherited_variables: tuple[str, ...]
    startup_timeout: float
    skill_path: Path
    instructions: str

    def config_override(self) -> str:
        # JSON string literals are also TOML basic strings. Serialize each
        # value explicitly; never interpolate executable code or shell text.
        environment = ",".join(
            f"{json.dumps(key)}={json.dumps(value)}" for key, value in self.environment.items())
        return ("mcp_servers.node_repl={command=" + json.dumps(str(self.command))
                + ",args=" + json.dumps(self.arguments)
                + ",env={" + environment + "}"
                + ",env_vars=" + json.dumps(self.inherited_variables)
                + f",startup_timeout_sec={self.startup_timeout}"
                + ",enabled_tools=[\"js\",\"js_reset\",\"turn_ended\"]}")


def _strings(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} 必须为字符串列表")
    return tuple(value)


def available_computer_use_pipes() -> tuple[str, ...]:
    """Enumerate pipe names only; never connect to or launch a native helper."""
    with os.scandir("\\\\.\\pipe\\") as entries:
        return tuple(entry.name for entry in entries
                     if re.fullmatch(r"codex-computer-use-[0-9a-fA-F-]{36}", entry.name))


def refresh_pipe_environment(environment: Mapping[str, str],
                             pipe_names: Callable[[], tuple[str, ...]]) -> dict[str, str]:
    """Refresh only a stale address; preserve permission settings and live selections."""
    result = dict(environment)
    if result.get("SKY_CUA_NATIVE_PIPE") != "1":
        return result
    names = pipe_names()
    prefix = "\\\\.\\pipe\\"
    addresses = tuple(prefix + name for name in names
                      if re.fullmatch(r"codex-computer-use-[0-9a-fA-F-]{36}", name))
    if result.get("SKY_CUA_NATIVE_PIPE_DIRECTORY") in addresses:
        return result
    if len(addresses) != 1:
        raise ValueError("Computer Use 通信管道不可用或存在多个候选，请在 Codex 中刷新 Computer Use 后重试")
    result["SKY_CUA_NATIVE_PIPE_DIRECTORY"] = addresses[0]
    return result


def load_windows_skill(codex_home: Path, *,
                       pipe_names: Callable[[], tuple[str, ...]] | None = None) -> WindowsSkill:
    """Read only the installed Computer Use skill and node_repl configuration."""
    try:
        config = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
        plugins = config.get("plugins", {})
        if isinstance(plugins, dict):
            plugin = plugins.get("computer-use@openai-bundled", {})
            if isinstance(plugin, dict) and plugin.get("enabled") is False:
                raise ValueError("Windows Computer Use 插件已被禁用")
        servers = config.get("mcp_servers", {})
        if not isinstance(servers, dict):
            raise ValueError("mcp_servers 必须为配置表")
        node = servers.get("node_repl")
        if not isinstance(node, dict) or node.get("enabled") is False:
            raise ValueError("未配置或未启用 node_repl")
        if "js" in node.get("disabled_tools", []) or (
            "enabled_tools" in node and "js" not in node["enabled_tools"]
        ):
            raise ValueError("node_repl js 工具已被禁用")
        command = node.get("command")
        if not isinstance(command, str) or not Path(command).is_file():
            raise ValueError("node_repl 程序不存在")
        raw_environment = node.get("env", {})
        if not isinstance(raw_environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in raw_environment.items()
        ):
            raise ValueError("node_repl env 必须为字符串配置表")
        timeout = node.get("startup_timeout_sec", 120)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError("node_repl 启动超时配置无效")
        packages = codex_home / "plugins/cache/openai-bundled/computer-use"
        candidates = list(packages.glob("*/skills/computer-use/SKILL.md"))
        if not candidates:
            raise ValueError("未安装 Control Windows apps 技能")
        skill = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        docs = skill.parent.parent.parent / "docs"
        instructions = "\n\n".join(
            path.read_text(encoding="utf-8") for path in (
                skill, docs / "guidance.md", docs / "api.md", docs / "confirmations.md"))
        environment = dict(raw_environment)
        if os.name == "nt" or pipe_names is not None:
            environment = refresh_pipe_environment(
                environment, pipe_names or available_computer_use_pipes)
        return WindowsSkill(
            command=Path(command), arguments=_strings(node.get("args", []), "node_repl args"),
            environment=environment,
            inherited_variables=_strings(node.get("env_vars", []), "node_repl env_vars"),
            startup_timeout=float(timeout), skill_path=skill, instructions=instructions)
    except (OSError, ValueError) as exc:
        message = (str(exc) if "通信管道" in str(exc) else
                   "Windows Computer Use 技能未就绪，请在 Codex 桌面应用中启用 Control Windows apps 及 node_repl 后重试。")
        raise TaskFailure(
            "windows_skill_unavailable",
            message,
            detail=str(exc)) from exc


WINDOWS_TOOL_NAMES = {"js": "windows_js", "js_reset": "windows_js_reset"}

WINDOWS_INSTRUCTIONS = """
当前操作系统是 Windows。使用下方已安装的 Control Windows apps 技能。
技能中的 node_repl JavaScript 会话由 desktop_windows_js（或原生 node_repl js）提供；
重置由 desktop_windows_js_reset 提供。它们使用同一个持久会话及 @oai/sky。
先在一个调用中初始化并 list_apps，读取返回，再选择目标和执行下一步。
每次操作遵守技能的观察、单次动作、立即重新观察流程。不要使用 macOS bundle ID。
不要调用 shell、Python UI 自动化、其他 MCP 或原生 helper 绕开这个技能。
不要把截图 data URL 打印成文本；截图由 node_repl 自动返回并显示。
nodeRepl.write 输出本次观察的窗口信息及无障碍文本，供完成依据和审计使用。
工具结果中的“本任务观察步骤编号”才是 evidence_steps，控件索引不是观察步骤。
必须遵守下方技能的限制及确认规则；需要用户现场确认时停止并报告 needs_input。
"""
