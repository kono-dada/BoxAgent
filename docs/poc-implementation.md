# 桌宠当前实现边界

核对日期：2026-10-05。已实现语音、通用桌面操作、Codex Runtime 上可切换的 OpenAI/DeepSeek 模型 Profile、独立的本地窗口摘要演示，以及用户显式控制的 Jev-Mem 长期记忆；自动多模态入库、Runtime 记忆注入、专注监控和时间线尚未实现。

当前实现以 `uv run --script scripts/pet.py` 启动，Python + AppKit 提供原生窗口。桌宠宿主的依赖声明放在这个 PEP 723 脚本，附带 uv 锁文件；MLX 使用独立 `.venv`，准备方法见 [环境准备](setup.md)。不编译 `.app`，不修改 MLX 实验环境。

## 模块之间的契约

| 文件 | 负责什么 | 不需要知道什么 |
| --- | --- | --- |
| `boxagent/core/` | 跨领域共享的 Event、状态、ID 和错误码 | 具体 Provider、AppKit、进程启动 |
| `boxagent/domain/<能力>/models.py` | 该领域的数据结构和不变量 | Provider 协议与 UI |
| `boxagent/domain/<能力>/contracts.py` | Service 所需 Repository、Backend 或外部能力端口 | 具体实现和装配方式 |
| `boxagent/domain/<能力>/service.py` | 注入 Contract 后实现 Conversation、Execution、Interaction、Memory 等用例 | 环境变量、AppKit 和进程发现 |
| `boxagent/application/assistant.py` | 组合多个 Domain Service，提供完整 BoxAgent 应用用例和统一生命周期 | 具体基础设施构造与原生控件 |
| `boxagent/agent/harness/` | 请求编译、SOUL 人格、上下文围栏、工具/权限/结果策略、任务生命周期与 Trace | App Server 进程协议、AppKit、具体模型供应商 |
| `boxagent/agent/runtime/contracts.py`、`models.py` | Agent Runtime Port、`RuntimeRequest` 与 Model Profile | 产品会话、记忆和 UI |
| `boxagent/agent/runtime/registry.py` | 将 `codex`/`deepseek` 选择解析为 Model Profile；Runtime 工厂由 Bootstrap 注入 | Codex 具体实现 |
| `boxagent/infrastructure/runtimes/codex/` | Codex native turn、App Server 进程、RPC、MCP 工具发现和 turn 清理 | 产品人格、上下文选择、任务是否完成 |
| `boxagent/infrastructure/runtimes/qwen/realtime.py` | 千问会话、`run_task()` 委托判定、显式记忆工具、回传结果仲裁、插话 | 目标应用操作流程、角色动画 |
| `boxagent/infrastructure/audio/pyaudio.py` | 麦克风与播放缓冲，发出实际播放状态 | 任务、窗口、模型提示词 |
| `boxagent/domain/memory/`、`boxagent/infrastructure/memory/jev.py` | 记忆产品策略与 Jev-Mem 子进程协议分离 | AppKit、语音会话与桌面操作 |
| `boxagent/domain/conversation/`、`boxagent/infrastructure/persistence/` | Final Message、Session Service 与 JSONL Repository | Provider 协议和 AppKit |
| `boxagent/interfaces/macos/` | Engine Bridge、状态投影、菜单、对话/桌宠/记忆窗口 | Provider、Jev 与 Computer Use 具体实现 |
| `boxagent/domain/perception/`、`boxagent/infrastructure/perception/qwen_mlx.py` | 感知生命周期与本地窗口摘要实现分离 | 任务执行、语音协议、角色素材 |
| `scripts/window_summary_worker.py` | 常驻 MLX 模型，JSONL 请求与响应 | AppKit、任务及提醒策略 |
| `scripts/jev_memory_worker.py` | 在独立环境中封装 Jev-Mem remember/query/forget/save/load | AppKit、语音会话与桌面操作 |
| `boxagent/bootstrap/settings.py`、`core/errors.py` | 不可变配置快照、密钥读取、日志脱敏和原子结果文件 | 角色渲染 |
| `boxagent/interfaces/macos/hotkey.py` | 注册单个全局快捷键 | 键盘行为收集 |
| `boxagent/interfaces/macos/pets/contracts.py` | 形象接口：尺寸、原生 view、`present(snapshot, now, pointer)` | 千问、Codex、工具调用 |
| `boxagent/interfaces/macos/pets/appearance.py` | 图集解码、动画时序、语义状态到动画的映射 | 任务派发和语音控制 |
| `boxagent/bootstrap/engine.py`、`desktop.py` | Engine 与 macOS Host 的唯一生产装配点 | 业务规则 |
| `boxagent/entrypoints/engine.py`、`desktop.py` | 参数解析、进程启动和信号处理 | 各模型、服务、UI 和图集的内部实现 |

形象层只消费状态，不能调用任务和语音。宿主传入角色附近的指针坐标；这些坐标不进入业务事件日志。换成 Live2D、3D 或 WebView 时，可以继续返回一个原生承载 view；入口替换一个形象实例即可。若未来桌面宿主也换掉，可另写快照消费者，后台不依赖原生 UI。

`voice`、`speaking`、`user_speaking` 与 `task` 是独立字段，任务状态不会因为进入说话动画而丢失。当前呈现优先级：待授权 → 用户正在说话 → 实际播放 → 后台工作 → 连接中 → 错误 → 成功 → 等待用户讲话 → 待机。

| 语义状态 | 当前角色动画 |
| --- | --- |
| 待机 | idle；指针移动后的短时间使用 v2 gaze |
| 聆听 | review |
| 说话 | waving 一次后 idle，标题仍显示正在说话 |
| 后台执行 | running |
| 连接／等待授权 | waiting |
| 完成 | jumping 一次后 idle |
| 失败 | failed 一次后 idle |

角色没有专用口型动画，不能把 waving 解释成音素同步。动画行号、FPS、循环规则取自站点 Playground，详见素材目录；不假设所有 Codex 客户端时序一致。

## 任务与语音的并发

AppKit 在主线程，后台 asyncio 在单独线程。后台通过队列发布带时间的快照，主线程更新窗口。音频 PCM 不进入这个队列。

文字入口经 `EngineBridge` 调用 `BoxAgentApplication.submit_text(goal)`，与语音共用 `InteractionService`。Qwen Realtime 先决定直接回答还是调用 `run_task()` 委托 `ExecutionService`；后台执行不阻塞前台继续聊天。只有确认接收后界面才清空已提交的文字；忙碌或异常保留草稿。组合输入尚未确认时不会触发提交。

对话面板可以获得输入焦点，小鸭窗口仍不可成为键盘窗口。面板宽度为 360pt，空闲高度 178pt，内容增加时伸展至最多 328pt，其后滚动显示；状态更新不重建输入控件。关闭图标使用系统符号，支持浅色和深色外观。形象适配器不受此次界面调整影响。

语音收到工具调用后，在独立协程中执行；Qwen 接收循环继续收音和接收新轮次。耗时调用前由语音提示词要求先简短回应，客户端同时发布已接受状态。提前开口已在实际合成输入测试中出现，但它仍是模型行为约定，不是服务端强制的音频协议保证。

后台委托有独立生命周期。语音回调等待它时使用 `asyncio.shield`：关闭语音连接不取消任务。语音工具结果保留真实 `call_id`，用户讲话、正在生成回复或仍有音频播放时排队；空闲后才回传并触发回复。已关闭的语音会话不能重用旧 `call_id`，因此结果保留在气泡，重新开麦后可以问“任务完成了吗”。

取消先设置停止标记，拒绝派发后续操作，再发 `turn/interrupt` 并释放本轮 Runtime 租约。已发给系统的动作无法撤销。每次只有一个后台委托；第二个请求得到忙碌反馈，不隐式积压。

## 通用自然语言执行

工具传输链仍为 Python → Codex App Server → Computer Use 客户端。Agent Runtime 始终是 Codex，模型 Profile 可选 OpenAI 或 DeepSeek。前台 Qwen 只调用 `run_task()` 表达委托决定；`ExecutionService` 从当前 Product Interaction 读取未经改写的 User Final，Harness 将其作为普通 Runtime Query，并与稳定策略、SOUL 人格和后续上下文编译为 `RuntimeRequest`。两种模型 Profile 都从 MCP 动态发现工具名、描述和输入 schema，没有按目标做分类或选择模板。

后台 Agent 自己选择应用、读取界面、规划和操作，再根据实际观察判断目标是否达成。工具输出同时保留无障碍文本和截图，并通过 Codex dynamic tools 的 `inputImage` 形态送入当前模型。操作编号、读回文本和截图保存在本次委托的目录中。

统一结果包含 `outcome`（completed / blocked / failed）、`summary` 和引用实际操作编号的 `evidence_steps`。这只是执行结果的通用格式，不是预设任务定义。Task Service 把 blocked 和 failed 如实呈现；模型运行结束不自动等于用户目标达成。

Harness Result Policy 检查证据编号确实存在、完成结论确实引用了工具观察，但不把模型判断伪装成程序严格核验。返回值使用 `assessment: agent`。计算器的表达式校验、专用提示词、固定应用标识和数字比较均已从运行时删除；原预实验算术脚本仍独立保留。

新场景只需要用户给出新目标。例如打开视频、调应用内设置、操作计算器，都通过同一条路径执行。权限按实际工具请求处理，不能把一个应用的许可直接复用到另一个应用。语音人格、后台通用执行提示词、形象映射分别维护。

## 本地窗口摘要

默认开启，每 15 秒尝试读取前台应用最上层的普通窗口，最长边缩至 960 像素；这不是辅助功能 API 所保证的精确焦点子窗口。常驻 MLX worker 串行处理，慢推理不积压，前台窗口身份变化时丢弃旧结果。同一窗口内部内容变化不触发版本校验。

摘要通过 `context.*` 事件与 `Snapshot.context_*` 字段供界面消费，不送入语音或执行器。收起对话时，独立气泡按文本长度分配约 2 秒打字动画；展开时直接显示全文。长文滚动，鼠标悬停延长停留。新摘要替换旧摘要，不维护历史气泡队列。

菜单可关闭或重新开启；关闭会取消观察并终止模型进程。`--context-interval 0` 为初始关闭，之后菜单开启使用 15 秒周期；开关不跨重启保存。`--context-size` 范围为 320–1920。输入临时截图在本轮结束后清理；强制杀进程或系统崩溃不保证执行清理。摘要保存在私有日志，模型错误写入 `context-worker.log`。

## 授权与结束

产品默认自动接受 Computer Use 的空表单操作许可，`--require-approval` 改为手动。非空表单和不支持的回调不自动批准。此选项与 macOS 的麦克风、屏幕录制、辅助功能权限不同。

OpenAI 与 DeepSeek 模型 Profile 都运行在 Codex Runtime 内，因此都使用同一套按本轮 thread/turn 标识执行的 `turn-ended` 清理。

Codex App Server 由 `BoxAgentApplication` 通过注入的 `CodexRuntimeHost` 持有并按需启动，任务结束只释放租约，应用退出才终止进程。模型、Developer Instructions 与动态工具 schema 保持一致时，多次请求向同一个 Codex Thread 追加 turn；任一稳定前缀发生变化时自动创建新的 Runtime Epoch，避免把不兼容配置写入旧上下文。工具按名称稳定排序，以提高前缀稳定性。Thread Binding 已保存到 Product Session；Engine 重启后优先通过 `thread/resume` 恢复。

每个任务退出时按其 thread / turn 标识调用官方 `turn-ended`，最多等待 8 秒，记录 `cursor_cleanup`。`notified` 只代表通知命令成功返回，不保证服务浮层已消失。不会自动重启共享服务。关闭整个桌宠时后台总等待另有限时，因此不应视为所有子进程清理已获长期运行验证。
