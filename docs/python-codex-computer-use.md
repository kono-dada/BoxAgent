# Python Computer Use 接入预实验（历史记录）

本文记录 2026-09-14 的实验接口及当时默认值。当前桌宠使用 `boxagent/executor.py`，不导入这里的计算器限定接口；已支持通用目标、图片回传和默认自动授权。当前使用方式见 [README](../README.md)，实现见 [当前实现](poc-implementation.md)。

> 最新进展：`gpt-5.6-luna` 的自然语言任务循环也已通过。必须补齐同版本的官方 `codex-code-mode-host`，并使用下文的 `--agent --dynamic-tool` 路径。此前“模型路径未通过”的记录已由这次实测更新。

2026-09-14，已打通以下执行链路，不需要 Node 或模型推理：

```text
桌宠的 Python 逻辑
  → Codex App Server 0.153.0 子进程
  → mcpServer/tool/call
  → 原装 SkyComputerUseClient / Computer Use 服务
  → macOS 真实 App
```

这是一项本机可行性验证。用户在独立系统 Terminal 启动 Python，成功收到计算器真实界面和截图；另一次本地实验实际点击清除、输入 `137+248`、按回车，并从界面和截图核验 `385`。未修改认证规则、签名或全局 Codex 配置。

## 为什么之前失败

有两个不同问题，不能混为一谈：

1. Python / Node 直接启动 Computer Use 客户端，在独立运行条件下遇到 `Sender process is not authenticated`。改由原装 Codex 子进程正常承接工具调用后，用户的独立 Terminal 实验成功。
2. Codex 0.154.0 在线程 MCP 初始化时增加 `capabilities.experimental = {"codex/auth-change": {}}`。当前 Computer Use 1000968 对这个字段报格式错误。工具库存查询不带该字段，因此能列出工具但线程不可用。

第二点已通过记录两种初始化消息、逐项对照真实客户端验证，并与公开的同类问题报告一致：[上游 issue #44458](https://github.com/openai/codex/issues/44458)。该字段涉及可选的账号状态变更通知；握手格式错误与执行器进程身份认证是不同阶段。

本次使用官方签名的 [Codex 0.153.0 发布包](https://github.com/openai/codex/releases/tag/rust-v0.153.0) 做版本兼容性对照后成功。源码参考：[初始化逻辑](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/rmcp_client.rs)、[通知逻辑](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/auth_changes.rs)。未来应复测修复后的新版；此处固定版本只代表已经验证的组合。

## 可复跑的预实验代码

- `scripts/codex_computer_use.py`：预实验异步 Python 接口，保持一个 Codex 会话，提供 `get_app_state`、`call`、`list_tools`。
- `scripts/computer_use.py`：上面接口复用的消息读取、进程清理等基础代码。直接运行它仍是之前未通过的原始执行器直连实验。
- `scripts/probe_codex_computer_use.py`：完整实验命令，保存运行日志、真实状态、截图和通过结果。
- `.runtime/codex-0.153.0/codex`：本机已准备并验证签名的官方运行时，已加入 Git 忽略。没有替换 PATH 中的 Codex。
- `.runtime/codex-0.153.0/codex-code-mode-host`：同版本官方工具运行组件，模型调用工具时需要；已补齐并通过代码签名验证。来源为官方发布页的 `codex-code-mode-host-aarch64-apple-darwin.tar.gz`，不是 Node 适配器。

从项目目录运行：

```bash
# 读取真实计算器，不调用模型。
uv run scripts/probe_codex_computer_use.py --allow-calculator-once

# 实际操作计算器并核验 385，不调用模型。
uv run scripts/probe_codex_computer_use.py --allow-calculator-once --calculate

# 交给 Luna 理解任务并自主决定计算器操作，会产生 Codex 模型用量。
uv run scripts/probe_codex_computer_use.py --allow-calculator-once --calculate --agent --dynamic-tool --model gpt-5.6-luna
```

桌宠集成时，可在 `scripts` 同目录的 Python 模块中使用：

```python
from codex_computer_use import CodexComputerUse

async def calculator_approval(params):
    # 这个示例只承接用户已经明确允许的本次计算器操作。
    return params.get("message") == "Allow ChatGPT to use Calculator?"

async def inspect_calculator():
    async with CodexComputerUse(approve=calculator_approval) as computer:
        state = await computer.get_app_state("com.apple.calculator")
        return computer.text(state)
```

当时的实验建议是让会话保持运行，避免每步重新启动进程；把授权请求交给用户界面决定，默认拒绝。接口默认只允许计算器。扩展其他 App 时，显式传入 `allowed_apps`，并实现相应授权交互。当前仅实测计算器的读取、点击、文字输入和按键，其他操作仍需逐项验收。

底层协议顺序：`initialize` → `initialized` → `thread/start` → 多次 `mcpServer/tool/call`。每次操作带当前 `threadId`、服务器名、工具名和参数。读取结果含辅助功能树和截图；点击索引必须取自最新状态。

## 已保存的证据

目录均在 `.runtime/private/artifacts/computer-use/python-app-server/`：

| 目录 | 内容 | 结果 |
| --- | --- | --- |
| `20260914-104833` | 0.154.0 直接工具调用 | 握手格式错误 |
| `20260914-105836` | 桌面自带 0.154.0-alpha.6.2 | 同样的格式错误 |
| `20260914-105922` | 诊断服务记录初始化请求 | 捕获库存与线程的字段差异；不是实际界面测试 |
| `20260914-110220` | 0.153.0 直接读取 | 真实界面与截图通过 |
| `20260914-110302` | 用户从独立系统 Terminal 启动 | 真实界面与截图通过，约 6.2 秒 |
| `20260914-110425` | 实际计算 `137+248` | 界面和截图显示 `385`，约 8.6 秒 |
| `20260914-124045` | Luna 模型，缺少配套 host | 模型工具调用失败，日志包含缺失文件路径 |
| `20260914-124144` | Luna 模型，补齐官方 host | 15 次模型工具调用，最终界面为 `385`，约 47.3 秒 |

耗时包含进程启动、线程建立、工具库存查询和操作，不是单个点击延迟。对外部 Terminal 的证据，启动来源来自用户明确说明，日志保存在本机。

## 桌宠如何分工

桌宠负责输入、角色表现和自己的决策。Python 将具体操作发给常驻 Codex 子进程，收到真实状态后更新桌宠界面或交给自己的模型继续判断。这条已验证路径没有 `turn/start`，不会为每个操作调用 Codex 模型。

自然语言路径现已通过，使用 `--agent --dynamic-tool --model gpt-5.6-luna`。Python 向 `thread/start` 注册 `calculator_ui` 动态工具，然后用 `turn/start` 提交任务。Luna 决定操作并触发 `item/tool/call`；Python 将工具参数交给同一个 App Server 的 `mcpServer/tool/call`，把真实返回结果交回模型。Python 不预先编排模型分支的计算步骤。本次模型依据辅助功能树判断，截图保存用于验收，没有作为模型工具结果输入。

实际观察到 15 次调用：读取、清除、读取、点击 1/3/7、读取、点击加号、读取、点击 2/4/8、读取、点击等号、最后读取。界面最终显示 `385`，模型报告完成。最后状态、截图和模型返回分别保存在该目录的 `state.txt`、`state.jpg`、`result.json`；`backend-reads.json` 保存原始界面返回。

早期失败原因之一是只安装了 Codex 主程序，遗漏其同版本工具宿主。日志明确报 `failed to spawn code-mode host ... No such file or directory`。关闭 code-mode host 不能替代安装配套组件。原始 MCP 自动暴露给模型的路径仍未验证成功；本次成功的是显式动态工具转接。

中止时先停止发送新动作并关闭本次会话；已发送的界面动作不保证回滚。此前取消实验也证实不能承诺即时撤销。未来产品应串行操作同一 App，并明确显示完成、失败或待授权状态。

## 剩余边界

已证明独立 Terminal 可以启动这条链路，不再要求 Python 是桌面 Codex 的子进程。尚未测试完全退出桌面应用后的依赖状态，也未测试全新机器部署、其他 App、长期常驻和打包后的桌宠进程。依赖当前安装的 Computer Use 组件、用户登录与系统授权；不代表获得执行器再分发许可或官方稳定的第三方 Computer Use SDK 支持。

官方接口文档：[App Server](https://learn.chatgpt.com/docs/app-server)、[Python SDK](https://learn.chatgpt.com/zh-Hans/docs/codex-sdk)。本实验直接使用 App Server 协议，未安装 Python SDK。
