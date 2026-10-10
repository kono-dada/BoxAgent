# 脚本用途

桌宠运行代码位于 `boxagent/`。本目录同时保留入口、验收工具和历史预实验；计算器相关规则只属于独立实验，不是当前桌宠的任务定义。

| 脚本 | 用途与副作用 |
| --- | --- |
| `pet.py`、`pet.py.lock` | 唯一产品入口与宿主依赖锁，`uv run --script scripts/pet.py` |
| `window_summary_worker.py` | 当前产品依赖的常驻 MLX 子进程，由观察适配器启动 |
| `setup-jev-mem.sh` | 为仓库内置 Jev-Mem 建立独立重依赖环境；不下载另一份源码 |
| `setup-aoq-sdk.sh` | 下载并校验固定版本 AOQ macOS Framework；二进制仅放在 `.runtime/`，不提交仓库 |
| `jev_mem_worker.py` | Jev-Mem JSONL Worker 命令入口；提供 health/remember/query/inspect/forget/save/shutdown，不直接运行在 AppKit 宿主进程 |
| `check_front_task_notification.py` | 真实 Qwen 前台 → DeepSeek/Codex 后台任务 → 继续聊天 → playback receipt 端到端冒烟；必须使用独立 `--data-dir` |
| `check_codex_native_history.py` | 真实 DeepSeek/Codex 冷启动与 Warm Thread 原生历史增量注入回归；必须使用独立 `--data-dir` |
| `check_memory_e2e.py` | Final User Message → durable Job → 仓库内置 Jev-Mem → Profile/Narrative → 跨 Session L2 召回与删除验收；默认使用临时目录，可选 mock/jev/auto backend；其中 `jev` 指 TypeSafe.ai JEV Decision backend |
| `set-key.zsh` | 交互写入 DashScope、Workspace、DeepSeek 与 TypeSafe.ai 凭据，不提交输出 |
| `download-qwen-mlx.zsh` | 下载模型至忽略目录，需网络、jq 与磁盘空间 |
| `import_vrm.mjs` | 导入本地 VRM；默认引用共享动作包，校验资源并拒绝覆盖现有形象 |
| `setup_vrm.sh` | 下载 Git LFS 中的模型和共享动作，使用 pnpm 安装并构建播放器 |
| `check_vrm_ui.py` | `pet.py --check-vrm-ui`，验证真实 VRM 渲染、3D 换装、失败保留和资源释放；语音与任务使用替身 |
| `check_pet_ui.py` | `pet.py --check-ui`，仅测试窗口、合成文字、记忆图和替身；结果在 `.runtime/pet/ui-check/` |
| `check_memory_graph_ui.py` | `pet.py --check-memory-graph-ui`，锁屏下用离屏 WKWebView 验证本地图加载、Canvas 像素、布局、筛选和原生消息桥；不使用 CU 或屏幕录制权限 |
| `check_window_summary.py` | `pet.py --check-context`，读取真实前台窗口，运行本地模型 |
| `smoke_pet.py` | `pet.py --smoke`，真实自然语言执行／语音检查；会操作应用和使用云端模型，`--dry-run` 仅将执行器替换为目标记录器，语音仍可调用云端 |
| `smoke_desktop.py` | `pet.py --smoke-desktop --audio /绝对路径/请求.wav`，真实语音、授权界面与桌面操作检查；显式要求 PCM16、16 kHz、单声道 WAV，不再依赖未提交的固定录音 |
| `voice-demo.py`、`calculator.py` | 独立语音预实验；工具是本地延迟算术函数，不操作计算器 App |
| `probe-qwen-mlx.zsh` | 独立图片推理，非桌宠当前 worker |
| `codex_computer_use.py`、`computer_use.py`、`probe_codex_computer_use.py` | 历史 Python 接入与计算器实验；当前 Task Kernel 与 Computer Use Adapter 不导入它们 |
| `probe-computer-use.mjs`、`computer-use-mcp-client.mjs`、`verify-computer-use*.mjs` | 历史 Node 对照／真实计算器实验，不属于产品启动链路；Node/pnpm 只用于构建当前 VRM 播放器 |
| `capture_mcp_initialize.py`、`diagnose_computer_use_transport.py`、`observe-computer-use.swift` | 历史握手、进程传输和前台状态诊断；不属于正常启动链路 |

`--smoke` 的 `--allow-app` 用于匹配测试收到的授权请求，不是隔离所有工具操作的系统沙箱；不要据此承诺模型无法访问其他应用。测试执行器明确关闭产品默认自动授权，让测试回调能够生效。原生真实验收也显式使用手动授权，用于检查授权 UI。

历史脚本保留用于复现技术选择与失败原因，不表示每条旧路径当前都可用。原始实验条件、失败路径见 `docs/` 中标注为历史的记录。原始界面、截图、日志、音频仅留在 `.runtime/`、`results/`、`logs/` 等忽略目录。
