# 本机环境准备

这是 macOS / Apple Silicon 的本机 POC，不是开箱即用的分发包。以下与 2026-09-19 的代码路径对应；已在现有机器运行，不代表已完成全新机器安装验收。

## 桌宠宿主与语音

准备 uv、Python 3.12 和 PortAudio。使用 Homebrew 的机器可以安装 `uv`、`portaudio`；PyAudio 首次构建还可能需要 Xcode Command Line Tools。桌宠的 Python 依赖由 `scripts/pet.py` 与 `scripts/pet.py.lock` 管理，不要把它们装入视觉 `.venv`。

当前支持的完整产品配置要求先在项目根目录完成凭据和三个运行组件的准备：

```sh
zsh scripts/set-key.zsh
./scripts/setup-aoq-sdk.sh
./scripts/setup-jev-mem.sh
./scripts/download-qwen-mlx.zsh
```

同时准备 Codex App Server、同版本 `codex-code-mode-host` 与 Computer Use 组件。全部完成后启动：

```sh
uv run --script scripts/pet.py --log-dir ./logs/boxagent
```

源码 checkout 默认将 Agent 后端运行在独立 Engine 进程，并监视后端 Python 文件。
后端变更会触发 Engine 重启，AppKit 窗口和菜单栏进程保持不变。正在执行的任务、语音和
审批会被中断；JSONL Product Session、Jev-Mem 长期记忆和本地配置仍可在新 Engine 中恢复。修改原生 UI 或 IPC
客户端时必须重启整个应用。通过 `BOXAGENT_ENGINE_WATCH=0` 可禁用后端源码监听。

配置脚本依次读取北京地域 DashScope API Key、百炼 Workspace ID、DeepSeek API Key 与 TypeSafe.ai API Key，并写入被 Git 忽略、仅本机可读的 `.env.local`。这些凭据是当前完整产品配置的一部分，不应提交到仓库。

外放全双工语音使用 AOQ，由原生 SDK 同时管理麦克风、扬声器、回声消除和降噪。安装：

```sh
./scripts/setup-aoq-sdk.sh
```

`scripts/set-key.zsh` 会同时写入以下配置：

```sh
BOXAGENT_QWEN_TRANSPORT=auto
BOXAGENT_DASHSCOPE_WORKSPACE_ID=<your-workspace-id>
BOXAGENT_DASHSCOPE_REGION=cn-beijing
```

`auto` 会在 Workspace ID 和两个 Framework 都就绪时选择 AOQ。WebSocket fallback 只用于开发诊断，不属于当前正式支持配置；降级原因写入 `<BOXAGENT_DATA_DIR>/logs/runtime/qwen/events.jsonl`。AOQ Token 开发期由 Engine 用 API Key 换取；正式发布应改为业务 AppServer 下发短期 Token，不应将 API Key 放入客户端安装包。

桌面任务默认要求 `BOXAGENT_TASK_PROVIDER=deepseek` 与 `DEEPSEEK_API_KEY`。Bootstrap 只把密钥以环境变量注入 Codex App Server 子进程；命令行和模型目录不包含真实密钥。Codex 通过仓库内的 DeepSeek 模型目录和 `wire_api="responses"` 调用 `deepseek-flash`，继续复用统一工具循环。

配置完成后，也可以从项目根目录显式启动 DeepSeek 规划路径：

```sh
./scripts/run-deepseek.sh
```

脚本不会执行 `.env.local`，只检查 `DEEPSEEK_API_KEY` 是否存在；具体密钥由 Python 配置层读取并传给 App Server 子进程。可用 `BOXAGENT_DEEPSEEK_MODEL` 覆盖默认的 `deepseek-flash`，其他参数可直接追加在脚本后。

优先从独立 Terminal 启动。直接 uv 运行没有独立应用权限身份；系统权限取决于启动终端及 Computer Use 组件。麦克风用于语音，屏幕录制用于本地截图；电脑操作还依赖执行组件获得所需系统权限。产品内的自动／手动授权不是 macOS 权限开关。

## 本地窗口摘要

运行 `./scripts/download-qwen-mlx.zsh` 准备 `models/qwen3.5-0.8b-mlx/`，并按 [MLX 预实验](qwen-mlx-probe.md) 准备视觉 `.venv`。下载脚本使用 `curl`、`jq`、`shasum`；模型来自远端 master，下载时校验远端提供的哈希，但没有固定模型仓库 revision，不能保证未来下载与历史模型逐字节相同。

当前完整产品配置要求本地视觉环境。宿主默认开启观察；模型缺失时实现会暂停观察并显示错误，以下关闭方式仅用于开发诊断：

```sh
uv run --script scripts/pet.py --log-dir ./logs/boxagent --context-interval 0
```

MLX 依赖目前记录了核心版本，但没有独立的完整依赖锁文件；全新环境的完全可复现性仍待验证。

## 电脑操作执行器

当前代码需要一套同时包含 `codex` 和同目录 `codex-code-mode-host` 的运行时，并按以下顺序选择：

1. `BOXAGENT_CODEX_BIN` 显式指定的 `codex`；
2. 项目内 `.runtime/codex-0.153.0/codex`；
3. `${CODEX_HOME:-$HOME/.codex}/plugins/.plugin-appserver/codex-cli/bin/codex`；
4. `PATH` 中的 `codex`（仅当同目录存在 `codex-code-mode-host` 时使用）。

如果需要准备项目内的固定版本，可按以下方式操作：

1. 从 [官方 0.153.0 发布页](https://github.com/openai/codex/releases/tag/rust-v0.153.0) 获取 Apple Silicon macOS 的 Codex 和同版本 `codex-code-mode-host` 压缩包。
2. 解压后将两个可执行文件放为 `.runtime/codex-0.153.0/codex`、`.runtime/codex-0.153.0/codex-code-mode-host`，保留执行权限；确认来源和签名，不修改签名或绕过系统校验。
3. 通过官方 Codex 客户端完成登录，并安装其 Computer Use 组件。程序读取 `${CODEX_HOME:-$HOME/.codex}/computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient`。

## Jev-Mem Worker

记忆适配层使用独立虚拟环境，不把 Torch、Transformers 和 FAISS 装入桌宠的 PEP 723 环境：

```sh
./scripts/setup-jev-mem.sh
```

Jev-Mem 源码已经随 BoxAgent 固定在 `boxagent/infrastructure/memory/jev_mem/`，不再从 `.runtime/` 克隆或导入源码。独立解释器默认位于 `.runtime/jev-mem-venv/bin/python`，只负责隔离重型依赖；新安装的持久化目录为 `.runtime/pet/memory/jev-mem`。可分别用 `BOXAGENT_JEV_MEM_PYTHON` 和 `BOXAGENT_JEV_MEM_CACHE` 覆盖；已有 `.runtime/pet/memory/jev` 时会继续读取旧 Store，避免升级后看不到已有记忆。

当前完整产品配置要求 `TYPESAFE_API_KEY` 与 `BOXAGENT_JEV_MEM_BACKEND=jev`，由 TypeSafe.ai 的 JEV Decision Model 完成记忆决策。`auto` 和 mock System-One 仅用于开发与协议验证，不代表生产记忆质量。

Qwen 冷恢复默认在完成消息累计到 18000 字符时异步生成 Context Checkpoint，单次摘要输入
上限为 32000 字符。可分别用 `BOXAGENT_QWEN_CHECKPOINT_TRIGGER_CHARS` 和
`BOXAGENT_CHECKPOINT_SOURCE_CHARS` 调整；后者不能小于前者。Checkpoint 通过独立的
Codex structured turn 生成，不进入桌面任务 Thread；自动记忆准入则把已落盘的原始
Final User Message 直接交给 Jev-Mem，不再调用 Codex 候选提取器，两者都不阻塞当前回复。

产品数据默认落在 `<BOXAGENT_DATA_DIR>`：Session 原始事件与 Checkpoint 位于
`conversations/`；Jev-Mem 是长期记忆唯一 Store，图、向量、关键词索引与审计位于
`memory/jev-mem/`；异步投递状态位于 `memory/jobs/ingestion.jsonl`；可重建 User Profile
投影位于 `memory/projections/profile.json`。Job 和 Profile 都不是第二份记忆真相。

桌宠启动时会后台预热记忆 Worker。首次启动仍需加载本地 embedding 模型，可能明显慢于后续请求。Final User Message 一经 Session Store 持久化就创建 durable ingestion job，Jev-Mem 完成是否保存、类型判断、关系构造和索引；Assistant 消息、屏幕摘要、原始音频和未经验证的桌面操作中间状态不会进入自动写入链路。显式“记住”带 `explicit` 标记并强制进入 Jev-Mem，但仍经过本地 Secret Filter。

启动桌宠后，可从 macOS 右上角 `◉` 菜单或桌宠右键菜单打开“记忆看板…”。看板通过同一个 Worker 读取 Jev-Mem 的脱敏、限量图快照；不开放本机端口，也不直接读取 `graph.json`。删除使用 Jev-Mem 的精确节点 ID，并在界面显示原生确认框。

持久化文件保留在本机，但启用真实后端时，Jev-Mem 会把决策所需的记忆文本和查询发送给 TypeSafe.ai JEV Decision Model；语音回忆命中的精简内容会通过 Function Calling 结果返回当前千问 Realtime 会话。不应将敏感信息写入当前记忆实现，也不应将这一路径描述为纯本地处理。

这些二进制与登录信息不随仓库提交。当前代码依赖这个本机组件布局及已验证版本组合，没有自动安装程序，也没有承诺稳定第三方 SDK 支持。版本兼容性历史见 [Computer Use 预实验](python-codex-computer-use.md)。

## 开发检查

```sh
# 本地替身测试，不调用模型或操作应用。
uv run --script scripts/pet.py --check

# 原生测试窗口与合成内容，不调用模型或操作其他应用。
uv run --script scripts/pet.py --check-ui

# 真实截取前台窗口并调用本地模型，需屏幕录制权限。
uv run --script scripts/pet.py --check-context
```

真实语音／电脑操作验收需单独执行，可能产生模型用量或改变应用状态，详见 [脚本目录说明](../scripts/README.md)。不要把全部实验脚本当作无副作用测试批量运行。
