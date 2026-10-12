# 本机环境准备

BoxAgent 的源码安装面向 Apple Silicon macOS。以下规范对应当前安装入口（2026-10-12）；现有机器已验证视觉环境重建和控制台安装/构建，尚未完成全新机器的完整安装与系统授权验收。

## 目录与依赖规则

源码、共享资产、依赖锁和安装脚本提交到 Git；本机依赖、缓存、下载模型和用户数据全部放在被忽略的 `.runtime/`，凭据只放在 `.env.local`。

| 路径 | 内容 | 管理规则 |
| --- | --- | --- |
| `.runtime/python/` | uv 管理的 Python 3.12.9 及本地命令链接 | 重建，不移动已有虚拟环境 |
| `.runtime/uv-cache/` | uv 缓存及桌宠宿主的脚本环境 | 宿主环境由 `pet.py.lock` 管理，不另建 `.venv-pet` |
| `.runtime/vision-venv/` | MLX 视觉环境 | 使用 `scripts/requirements/vision.lock.txt` 精确同步 |
| `.runtime/jev-mem-venv/` | 独立记忆环境 | 使用 `scripts/requirements/jev-mem.lock.txt` 精确同步 |
| `.runtime/cache/` | Hugging Face、XDG、pnpm 缓存 | 由统一入口设置路径，pnpm 同时有项目配置 |
| `.runtime/models/` | 本地视觉模型 | 下载后逐文件校验仓库中的 SHA256 清单 |
| `.runtime/aoq-sdk/1.3.0/` | AOQ Framework 与下载包 | 固定版本及压缩包 SHA256 |
| `.runtime/codex-0.153.0/` | Codex 与同版本工具宿主 | 从官方发布获取，并检查签名 |
| `.runtime/pet/`、`.runtime/pets/` | 会话、记忆、Skill、人格、设置、个人形象 | 用户数据，不能当缓存清理 |
| `.runtime/checks/` | 自动化检查产物 | 可在确认没有检查运行后清理 |
| `.runtime/private/` | 历史用户记录及私人原稿 | 只保存在本机，不提交 |

视觉与记忆依赖锁是从现有可用环境导出的完整固定版本清单，适用于 Python 3.12.9 / Apple Silicon macOS；不包含软件包文件哈希，也不宣称支持其他平台。更新依赖时显式修改清单并重跑检查，不在日常安装中自动升级。

安装与启动脚本共用 `scripts/runtime-env.sh`。路径优先级是进程环境、`.env.local`、仓库默认值；`.env.local` 只读取指定变量的字面值，不执行命令，也不展开 `$HOME` 或 `$(...)`。覆盖路径时填写绝对路径，示例见 `.env.local.example`。

保留已在使用的 `uv-cache`、记忆环境和运行组件路径，避免迁移破坏现有进程。虚拟环境的激活文件和命令入口含绝对路径；更换仓库位置后应重新安装，不能直接搬运环境。

## 桌宠宿主与语音

准备 Xcode Command Line Tools、uv、Node.js、pnpm、PortAudio、pkg-config、Git LFS 与 jq。使用 Homebrew 的机器可执行：

```sh
brew install uv node pnpm portaudio pkg-config git-lfs jq
```

这是系统工具安装；后续 Python、项目依赖、模型和缓存由仓库内安装入口管理。本机验证使用 uv 0.9.13、pnpm 10.33.2；前端的 `packageManager` 固定为 pnpm 10.33.2。先按下文准备官方 Codex、工具宿主与 Computer Use 组件，再执行安装。

当前支持的完整产品配置要求先在项目根目录完成凭据、运行组件和 3D 资产的准备：

```sh
zsh scripts/set-key.zsh
./scripts/install.sh
```

`install.sh` 可重复执行：准备仓库内 Python，以锁文件同步宿主、视觉和记忆依赖，补齐缺失的 AOQ Framework，校验或下载模型，拉取 Git LFS 资产并构建播放器，安装并构建 React / Electron 伙伴控制台，最后执行安装检查。它不会修改密钥、删除用户数据、自动登录账号或替用户设置系统权限。

只检查安装与配置，不调用云端、不操作应用：

```sh
./scripts/install.sh --check
```

检查覆盖固定依赖版本、模型文件摘要、Framework、播放器、控制台构建与 Electron 可执行文件、运行组件签名与所选决策服务凭据是否存在；不验证密钥有效性、云端连通性、系统权限或可见 UI。缺少项目时返回非零状态。安装和检查通过后启动：

```sh
./scripts/run-pet.sh
```

源码 checkout 默认将 Agent 后端运行在独立 Engine 进程，并监视后端 Python 文件。
后端变更会触发 Engine 重启，AppKit 窗口和菜单栏进程保持不变。正在执行的任务、语音和
审批会被中断；JSONL Product Session、Jev-Mem 长期记忆和本地配置仍可在新 Engine 中恢复。修改原生 UI 或 IPC
客户端时必须重启整个应用。通过 `BOXAGENT_ENGINE_WATCH=0` 可禁用后端源码监听。

配置脚本读取北京地域 DashScope API Key、百炼 Workspace ID、DeepSeek API Key，以及可选的 TypeSafe.ai API Key，写入被 Git 忽略、仅本机可读的 `.env.local`。默认决策服务复用 DashScope Key / Workspace，无需 TypeSafe Key；凭据不应提交到仓库。

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

脚本使用同一个启动入口，只检查 `DEEPSEEK_API_KEY` 是否存在；具体密钥由 Python 配置层读取并传给 App Server 子进程。可用 `BOXAGENT_DEEPSEEK_MODEL` 覆盖默认的 `deepseek-flash`，其他参数可直接追加在脚本后。双击 `启动桌宠.command` 与 Codex 项目运行按钮也调用同一入口。

优先从独立 Terminal 启动。直接 uv 运行没有独立应用权限身份；系统权限取决于启动终端及 Computer Use 组件。麦克风用于语音，屏幕录制用于本地截图；电脑操作还依赖执行组件获得所需系统权限。产品内的自动／手动授权不是 macOS 权限开关。

## 本地窗口摘要

安装入口通过 `setup-vision.sh` 准备 `.runtime/vision-venv/`，通过 `download-qwen-mlx.zsh` 准备 `.runtime/models/qwen3.5-0.8b-mlx/`。后者校验仓库中固定的 13 个文件摘要，已有匹配文件不会重新下载，新文件在 `.part` 中校验通过后才发布。

下载源仍为 ModelScope 的 master；若上游替换文件，下载将因固定摘要不匹配而失败，不会静默接受新版本。完整的新机器复现仍取决于上游保留对应文件。原始模型来源与推理实验见 [MLX 预实验](qwen-mlx-probe.md)，其中旧路径属于历史记录。

桌宠默认关闭屏幕总结，菜单开启后才启动视觉进程；也可以显式启用：

```sh
./scripts/run-pet.sh --context-interval 15
```

用 `BOXAGENT_PERCEPTION_PYTHON` 与 `BOXAGENT_PERCEPTION_MODEL` 覆盖本机路径。不要把宿主的语音和 AppKit 依赖装进视觉环境。

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

真实记忆后端使用 `BOXAGENT_JEV_MEM_BACKEND=jev`。`BOXAGENT_DECISION_PROVIDER=auto` 在配置 DashScope Key / Workspace 时优先阿里云百炼 `decision-model-preview`，否则选择 TypeSafe.ai；可显式设置 `aliyun` 或 `typesafe`，后者需要 `TYPESAFE_API_KEY`。记忆决策与回合前意图估计共用所选服务，分别使用 Worker。意图参考默认开启，最多等待 400 ms，`BOXAGENT_INTENT_REFERENCE=0` 仅关闭意图参考。mock System-One 只验证协议，不代表生产记忆质量。

Qwen 冷恢复默认在完成消息累计到 18000 字符时异步生成 Context Checkpoint，单次摘要输入
上限为 32000 字符。可分别用 `BOXAGENT_QWEN_CHECKPOINT_TRIGGER_CHARS` 和
`BOXAGENT_CHECKPOINT_SOURCE_CHARS` 调整；后者不能小于前者。Checkpoint 通过独立的
Codex structured turn 生成，不进入桌面任务 Thread；自动记忆准入则把已落盘的原始
Final User Message 直接交给 Jev-Mem，不再调用 Codex 候选提取器，两者都不阻塞当前回复。

产品数据默认落在 `<BOXAGENT_DATA_DIR>`：新安装的 Session 事件、Checkpoint 与上下文账本合并于
`sessions/<id>/session.v1.jsonl`，任务位于 `sessions/<id>/tasks/<task_id>/`；已有 `conversations/index.json` 的安装需离线迁移才切换布局。Jev-Mem 是长期记忆唯一 Store，图、向量、关键词索引与审计位于
`memory/jev-mem/`；异步投递状态位于 `memory/jobs/ingestion.jsonl`；可重建 User Profile
投影位于 `memory/projections/profile.json`。Job 和 Profile 都不是第二份记忆真相。

桌宠启动时会后台预热记忆 Worker。首次启动仍需加载本地 embedding 模型，可能明显慢于后续请求。Final User Message 一经 Session Store 持久化就创建 durable ingestion job，Jev-Mem 完成是否保存、类型判断、关系构造和索引；Assistant 消息、屏幕摘要、原始音频和未经验证的桌面操作中间状态不会进入自动写入链路。显式“记住”带 `explicit` 标记并强制进入 Jev-Mem，但仍经过本地 Secret Filter。

启动桌宠后，从菜单栏星光图标或桌宠右键菜单打开「伙伴控制台…」，在长期记忆页查看图、列表和节点关系。React / Electron 页面通过本机 `engine.sock` 调用同一个 Worker 的脱敏、限量图快照，不直接读取 `graph.json`。删除使用精确节点 ID，并在控制台确认。旧 AppKit 记忆、Skill、角色管理窗口已移除。

完整安装已包含控制台；单独准备依次执行 `pnpm --dir web/panel install --frozen-lockfile`、`pnpm --dir web/panel build`、`pnpm --dir web/panel start`。菜单和 `start` 直接载入构建产物，避免每次打开都检查/构建；修改前端源码后用 `pnpm --dir web/panel dev` 重建并打开。如设置 `BOXAGENT_DATA_DIR`，控制台与桌宠须使用同一目录。设置 → 界面偏好提供中英文及浅色 / 深色 / 跟随系统。架构与通道详见 [前后端连接](BoxAgent-架构重构方案.md#44-伙伴控制台与前后端连接)。

持久化文件保留在本机，但启用真实后端时，Jev-Mem 会把决策所需的记忆文本和查询发送给所选决策服务（默认阿里云，也可选 TypeSafe.ai）；回忆命中的精简内容进入当前千问 Realtime 会话的 Query 参考包，显式深度召回也可通过 Function Calling 返回。不应将这一路径描述为纯本地处理。

## 旧会话迁移与回滚

先退出桌宠和独立 Engine，再运行：

```sh
uv run --python 3.12 python scripts/migrate_sessions_v1.py --data-dir .runtime/pet
# 回滚时导出迁移后的最新正式历史，切回兼容布局
uv run --python 3.12 python scripts/migrate_sessions_v1.py --data-dir .runtime/pet --rollback
```

自定义数据目录须替换 `--data-dir`。迁移复制并校验，保留源目录；回滚生成 `conversations-unified-rollback/`，不撤销电脑动作、不删除长期记忆。Engine 活跃时拒绝操作。新安装直接使用统一布局，不需要迁移。详情见 [交付与迁移边界](需求/2026-10-12-分支交付与PR验收.md)。

这些二进制与登录信息不随仓库提交。仓库安装入口检查本机组件，官方账号登录与 Computer Use 安装仍需要单独完成；当前依赖已验证的组件组合。版本兼容性历史见 [Computer Use 预实验](python-codex-computer-use.md)。

## 开发检查

```sh
# 本地替身测试，不调用模型或操作应用。
./scripts/run-pet.sh --check

# 原生测试窗口与合成内容，不调用模型或操作其他应用。
./scripts/run-pet.sh --check-ui

# 真实截取前台窗口并调用本地模型，需屏幕录制权限。
./scripts/run-pet.sh --check-context
```

真实语音／电脑操作验收需单独执行，可能产生模型用量或改变应用状态，详见 [脚本目录说明](../scripts/README.md)。不要把全部实验脚本当作无副作用测试批量运行。
