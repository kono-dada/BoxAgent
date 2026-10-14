# BoxAgent

> A full-duplex, memory-native desktop companion for macOS.

BoxAgent 是一个常驻在 Mac 桌面的 AI 伙伴。它可以自然对话、记住长期偏好，并在后台操作电脑；耗时任务不会阻塞当前聊天，完成后会主动回来告诉你结果。

> [!IMPORTANT]
> BoxAgent 目前是面向 macOS / Apple Silicon 的开发版本，不是开箱即用的正式发行包。运行前需要准备模型凭据、Codex Computer Use 组件和相应的 macOS 系统权限。

## 产品演示

https://github.com/user-attachments/assets/57a0d029-2d58-42e5-9f1e-dfe7e535314e

这段历史版本的 5 分钟真人录屏包含声音，展示全双工语音、跨 Session 长期记忆、后台音乐操作、任务结果通知，以及角色、记忆和 Skill 管理。当前形象已升级为 3D VRM。

## 它能做什么

| 能力 | 体验 |
| --- | --- |
| 全双工语音 | 随时说话、自然插话；AOQ 优先提供回声消除与降噪，WebSocket 可降级使用 |
| 后台 Computer Use | 对话不中断的情况下操作 macOS 应用，任务完成后主动通知 |
| 统一会话上下文 | 文字、语音、Qwen 与任务 Runtime 共享同一个持久 Product Session |
| 长期记忆 | Jev-Mem 自动判断值得保存的信息，支持画像、叙事记忆和主动召回 |
| Skill 系统 | 管理内置与用户 Skill，也可以从真实任务轨迹生成并安装 Skill 草稿 |
| 桌宠与伙伴控制台 | Electron 统一管理 3D 桌宠、React 对话小窗、任务展示和伙伴控制台 |

例如，你可以对它说：

- “帮我播放一首音乐，完成后告诉我。”
- “去知乎找几篇关于 Agent Memory 的高赞文章，我先继续和你聊。”
- “我更喜欢简洁一点的回答。”
- “把刚才这套操作沉淀成一个 Skill。”

## 工作方式

BoxAgent 将“陪伴式交互”和“耗时执行”分开，但由同一个 Session 保持连续体验：

```mermaid
flowchart LR
    U[用户：文字 / 语音] --> Q[Qwen Realtime<br/>前台交互]
    Q -->|普通对话| U
    Q -->|桌面任务| H[Harness]
    H --> R[Codex Task Runtime<br/>Computer Use]
    R --> O[持久 Outbox]
    O --> U

    S[(Product Session)] <--> Q
    S <--> H
    M[(Jev-Mem)] <--> H
    K[(Skills)] --> H
```

- 所有文字和语音首先进入 Qwen Realtime，普通聊天直接回答。
- 需要操作电脑时，完整用户目标会异步委托给任务 Runtime。
- Harness 负责编译当前时间、Session 历史、相关记忆、Skill 与安全策略。
- 后台结果先写入持久 Outbox，再通过语音、桌宠未读状态或系统通知送达。
- Final User Message 落盘后异步进入 Jev-Mem，不阻塞当轮回复。

桌面界面统一由 Electron 管理，React / TypeScript / Vite 提供对话小窗和控制台，Three.js / three-vrm 提供透明桌宠。界面通过受限 IPC 调用 Python Engine；形象资源与 Engine 生命周期由无界面的 Python 本机服务管理。详细连接方式见 [前后端连接](docs/BoxAgent-架构重构方案.md#44-伙伴控制台与前后端连接)。

更完整的数据流和模块边界见 [架构重构方案](docs/BoxAgent-架构重构方案.md)、[Conversation Context 设计](docs/BoxAgent-Phase3-Conversation-Context-设计.md) 与 [Memory 设计](docs/BoxAgent-Phase4-Memory-设计.md)。

## 快速开始

### 1. 准备环境

- macOS / Apple Silicon
- Git LFS、Node.js 与 pnpm（用于 3D 播放器和 Electron 控制台）
- Python 3.12、[uv](https://docs.astral.sh/uv/) 与 PortAudio
- 北京地域 DashScope API Key 与百炼 Workspace ID
- DeepSeek API Key；TypeSafe.ai API Key 仅在显式选择该决策服务时需要
- AOQ macOS SDK、Jev-Mem Worker 与本地 MLX 视觉模型
- Codex App Server、`codex-code-mode-host` 与 Computer Use 组件

完整安装步骤、运行时路径和权限说明见 [本机环境准备](docs/setup.md)。

### 2. 配置凭据

```sh
zsh scripts/set-key.zsh
```

该脚本读取 DashScope API Key、百炼 Workspace ID、DeepSeek API Key，并可选读取 TypeSafe.ai API Key，生成仅本机可读的 `.env.local`。默认决策模型使用阿里云百炼，与 Qwen 共用 DashScope Key 和 Workspace。

### 3. 安装运行组件

```sh
./scripts/install.sh
```

安装前还需要准备同版本的 Codex App Server、`codex-code-mode-host` 与 Computer Use 组件，具体路径见 [本机环境准备](docs/setup.md)。安装入口按锁文件同步依赖，将环境、模型和缓存放在 `.runtime/`，构建 3D 播放器与伙伴控制台，最后检查安装与配置；以后可用 `./scripts/install.sh --check` 单独复查。

### 4. 启动

```sh
./scripts/run-pet.sh
```

也可以双击 `启动桌宠.command`，或在终端运行 `./启动桌宠.command`；两者都调用同一个启动脚本，支持追加参数。启动优先复用本机已安装的 Python 3.12.x，不自动下载 Python；未安装时会提示先运行安装入口。首次同步依赖仍可能需要联网，并可能请求麦克风、屏幕录制与辅助功能权限。

启动时只显示 3D 桌宠，默认形象为 Zome。聊天面板和屏幕总结默认关闭。聊天面板可手动打开；屏幕总结可从伙伴控制台的「设置 → 语音与桌面」开启，开启后默认每 15 秒检查前台窗口。

### 5. 开始使用

- `Control + Option + Space`：开启或关闭麦克风
- 点击桌宠：展开或收起对话
- 拖动桌宠：移动位置
- 右键桌宠或点击菜单栏星光图标：打开伙伴控制台、新建会话或退出 BoxAgent
- 伙伴控制台左下角「设置」：界面语言（中文 / English）、浅色 / 深色 / 跟随系统、3D 形象、角色、语音与桌面、诊断
- 对话小窗上滑查看历史时，点击右下角箭头回到最新消息
- 伙伴控制台「设置 → 3D 形象 → 桌宠大小」：50%～600% 连续缩放，恢复默认回到 100%；重启和换装保留设置，实际尺寸受屏幕范围限制
- “取消任务”或“停止任务”：停止当前后台任务的后续操作

## 配置

| 模块 | 默认行为 | 主要配置 |
| --- | --- | --- |
| Qwen Realtime | AOQ 提供全双工语音、回声消除与降噪；WebSocket 仅作诊断降级 | `DASHSCOPE_API_KEY`、`BOXAGENT_DASHSCOPE_WORKSPACE_ID` |
| 桌面任务 | DeepSeek 负责规划，Codex Runtime 提供工具循环与 Computer Use | `DEEPSEEK_API_KEY`、`BOXAGENT_CODEX_BIN` |
| 决策模型 / Jev-Mem | 默认阿里云 `decision-model-preview`，用于记忆准入/检索和当前 Query 的意图参考 | `BOXAGENT_DECISION_PROVIDER=auto`、DashScope Key / Workspace、`BOXAGENT_JEV_MEM_BACKEND=jev`；意图参考可用 `BOXAGENT_INTENT_REFERENCE=0` 关闭 |
| 本地屏幕总结 | 启动时关闭，可从控制台设置开启；开启后默认每 15 秒检查前台窗口 | `./scripts/download-qwen-mlx.zsh`、`--context-interval`（正数可在启动时开启） |
| 人格 | 优先读取本地私有人格，否则使用内置默认人格 | `BOXAGENT_SOUL_FILE` |
| 用户 Skill | 默认保存在本地运行目录并热同步 Runtime | `BOXAGENT_SKILLS_DIR` |

更多环境变量和依赖边界见 [docs/setup.md](docs/setup.md)。

## 安全与隐私

BoxAgent 能真实操作用户电脑，因此安全边界是产品能力的一部分，而不是模型提示词里的附加说明。

- Session、任务轨迹、记忆、个人导入的 VRM 和本地配置默认保存在 `.runtime/`，该目录不会提交到 Git。
- 日志可能包含任务目标、应用界面文字和工具参数，不应直接上传或公开分享。
- 当前版本默认自动允许 Computer Use 工具请求；使用 `--require-approval` 可恢复逐次确认。
- 不可逆操作仍依赖任务策略和最终状态核验；当前版本不是完整操作系统沙箱。
- 启用真实 Jev-Mem backend 时，相关文本会发送给所选决策服务（默认阿里云，亦可选 TypeSafe.ai）；Qwen、任务模型与 Profile 模型也会接收各自完成请求所需的上下文。
- 屏幕总结截图仅在本地推理期间临时存在，当前不会自动进入长期记忆。

请只在你信任的机器和测试账号中运行开发版。详细的数据落盘与审计说明见 [运行记录与上下文审计](docs/BoxAgent-运行记录与上下文审计.md)。

## 项目状态

### 已实现

- Qwen 统一文字 / 语音前台与 AOQ 全双工链路
- 异步 Computer Use、取消、终态核验与持久通知 Outbox
- 持久 Product Session、Runtime 恢复 Checkpoint 与跨 Runtime 上下文
- Jev-Mem 自动写入、Profile、Narrative、L2 direct recall 与 L3 deep recall
- React / Electron 伙伴控制台：记忆图、上下文请求控制台、会话、Skill、任务与设置；中英文和浅色 / 深色 / 系统主题
- React 对话小窗：Markdown、历史回到底部、任务授权 / 停止；旧 AppKit 窗口与 WebKit UI 已移除
- 仅支持 3D VRM 形象、本地导入与运行中换装；共享模型和动作通过 Git LFS 管理
- 本地 MLX 前台窗口总结

### 仍在推进

- 新用户 Onboarding、依赖检测与正式应用分发
- 桌面任务队列、并发调度和更稳定的第三方应用适配
- 记忆冲突消歧、画像质量评测与多尺度 consolidation
- 脚本型 Skill 沙箱、ZIP / Git 导入与 Skill 市场
- 持续委托、活动时间线和长期运行可靠性

本分支交付、迁移与最新验证边界见 [分支交付与 PR 验收](docs/需求/2026-10-12-分支交付与PR验收.md)；早期能力验证见 [POC 验收记录](docs/poc-verification.md)。

## 项目结构

```text
boxagent/
├── entrypoints/       # 进程入口
├── bootstrap/         # Desktop / Engine 生产装配
├── application/       # 用例编排
├── domain/            # Session、Task、Memory、Skill 等领域模型与服务
├── agent/
│   ├── harness/       # 上下文、策略与 Runtime 编排
│   └── runtime/       # Runtime 合同与模型 Profile
├── infrastructure/   # Qwen、Codex、Jev-Mem、持久化等适配器
└── interfaces/        # Engine API、桌面资源与进程监督
web/
├── panel/             # React / TypeScript / Vite + Electron 桌宠、小窗和控制台
└── vrm/               # Three.js / three-vrm 共享角色播放器
```

依赖方向为 `entrypoints → bootstrap → application/domain/agent ← infrastructure`。领域层不直接依赖 Qwen、Codex、Jev-Mem 或 AppKit 的具体实现。

## 开发与测试

```sh
# 本地替身检查，不调用模型或操作其他应用
uv run --script scripts/pet.py --check

# 原生 UI 检查
uv run --script scripts/pet.py --check-ui

# 测试套件
uv run --python 3.12 --with pytest --with 'pillow>=11,<13' --with 'websockets>=15,<17' --with 'pyaudio>=0.2.14' python -m pytest -q

# 控制台构建与前端测试
pnpm --dir web/panel install
pnpm --dir web/panel build
pnpm --dir web/panel test

# 打开控制台（需先启动桌宠，或独立 Engine）
pnpm --dir web/panel start

# 渐进式静态类型检查（范围见 pyrightconfig.json）
uvx --from pyright==1.1.414 pyright
```

真实语音、Computer Use 和屏幕总结测试可能产生模型用量、读取屏幕或改变应用状态。运行前请先阅读 [脚本用途与副作用](scripts/README.md)。

## 文档

| 主题 | 文档 |
| --- | --- |
| 安装与配置 | [本机环境准备](docs/setup.md) |
| 当前架构与前后端连接 | [架构重构方案](docs/BoxAgent-架构重构方案.md) |
| 伙伴控制台与界面偏好 | [Panel README](web/panel/README.md) |
| 会话与上下文 | [Phase 3：Conversation Context](docs/BoxAgent-Phase3-Conversation-Context-设计.md) |
| 长期记忆 | [Phase 4：Memory](docs/BoxAgent-Phase4-Memory-设计.md) |
| 运行记录与审计 | [运行记录与上下文审计](docs/BoxAgent-运行记录与上下文审计.md) |
| 需求状态与本次交付 | [需求路线图](docs/需求/README.md) · [PR 验收](docs/需求/2026-10-12-分支交付与PR验收.md) |
| 3D 形象与共享资产 | [VRM 桌宠](docs/vrm.md) |
| Computer Use | [Python Computer Use](docs/python-codex-computer-use.md) |
| 语音链路 | [Qwen 全双工实验](docs/qwen-full-duplex-summary.md) |
| 本地视觉 | [Qwen MLX 实验](docs/qwen-mlx-probe.md) |
| 验收结果 | [POC Verification](docs/poc-verification.md) |

## Acknowledgements

BoxAgent 的当前实现建立在 Qwen Realtime / AOQ、OpenAI Codex Computer Use、Jev-Mem、DeepSeek、MLX，以及 Three.js / three-vrm之上。

---

如果你正在尝试运行或参与开发，请从 [本机环境准备](docs/setup.md) 开始；如果你想先理解设计，推荐依次阅读 [架构](docs/BoxAgent-架构重构方案.md)、[上下文](docs/BoxAgent-Phase3-Conversation-Context-设计.md) 和 [记忆](docs/BoxAgent-Phase4-Memory-设计.md)。
