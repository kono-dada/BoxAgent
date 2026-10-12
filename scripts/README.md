# 脚本用途

桌宠运行代码位于 `boxagent/`。本目录同时保留入口、验收工具和历史预实验；计算器相关规则只属于独立实验，不是当前桌宠的任务定义。

| 脚本 | 用途与副作用 |
| --- | --- |
| `install.sh`、`runtime-env.sh` | 统一安装、路径配置与缓存；包含播放器和伙伴控制台构建；`install.sh --check` 仅检查本机安装 |
| `启动桌宠.command`、`run-pet.sh`、`pet.py`、`pet.py.lock` | Finder 双击或终端 `./启动桌宠.command` 启动；复用已安装的 Python 3.12.x，缺失时提示安装，参数原样传递，宿主依赖按锁文件同步 |
| `setup-vision.sh`、`requirements/*.lock.txt` | 用 uv 精确同步视觉与记忆环境的完整固定版本 |
| `check_install.py` | 离线检查依赖版本、模型摘要、运行组件签名与凭据是否存在，不证明云端或 UI 可用 |
| `check_electron_ui.py` | 真实 Electron + VRM + Python 资源服务隔离验收；Engine 使用替身，不调用模型 / 麦克风 / 屏幕；旧 `--check-ui`、`--check-vrm-ui`、`--check-pet-size-ui`、`--smoke-desktop` 均映射此检查 |
| `window_summary_worker.py` | 当前产品依赖的常驻 MLX 子进程，由观察适配器启动 |
| `setup-jev-mem.sh` | 为仓库内置 Jev-Mem 建立独立重依赖环境；不下载另一份源码 |
| `setup-aoq-sdk.sh` | 下载并校验固定版本 AOQ macOS Framework；二进制仅放在 `.runtime/`，不提交仓库 |
| `jev_mem_worker.py` | Jev-Mem JSONL Worker 命令入口；提供 health/remember/query/inspect/forget/save/shutdown，不直接运行在 Electron renderer |
| `check_front_task_notification.py` | 真实 Qwen 前台 → DeepSeek/Codex 后台任务 → 继续聊天 → playback receipt 端到端冒烟；必须使用独立 `--data-dir` |
| `check_codex_native_history.py` | 真实 DeepSeek/Codex 冷启动与 Warm Thread 原生历史增量注入回归；必须使用独立 `--data-dir` |
| `check_memory_e2e.py` | Final User Message → durable Job → 仓库内置 Jev-Mem → Profile/Narrative → 跨 Session L2 召回与删除验收；默认使用临时目录，可选 mock/jev/auto backend；其中 `jev` 指 TypeSafe.ai JEV Decision backend |
| `set-key.zsh` | 交互写入 DashScope、Workspace、DeepSeek 与 TypeSafe.ai 凭据，不提交输出 |
| `download-qwen-mlx.zsh` | 下载模型至忽略目录，需网络、jq 与磁盘空间 |
| `import_vrm.mjs` | 导入本地 VRM；默认引用共享动作包，校验资源并拒绝覆盖现有形象 |
| `setup_vrm.sh` | 下载 Git LFS 中的模型和共享动作，使用 pnpm 安装并构建播放器 |
| `check_window_summary.py` | `pet.py --check-context`，读取真实前台窗口，运行本地模型 |
| `experiments/window_summary_eval.py` | FIX-01 离线对比，默认生成 12 张合成界面；`--images` 可指定本机截图。与生产 worker 共用提示词和配置，结果及样本哈希写入 `.runtime/window-summary-eval/` |
| `migrate_sessions_v1.py` | 持 Engine 锁与 Session 锁复制、校验旧会话和任务；要求 Engine 退出；`--rollback` 导出全部正式历史再切换，原数据保留 |
| `intent_worker.py` | 生产独立 JEV 概率参考 Worker；默认开启意图参考，可用 `BOXAGENT_INTENT_REFERENCE=0` 关闭；不操作电脑，不写入长期记忆 |
| `experiments/qwen_intent_ab.py` | 32条仓库内合成输入的配对 AOQ/JEV A/B；真实云端模型费用，麦克风/播放关闭，工具全部 stub，不操作电脑；输出新临时目录 |
| `experiments/companion_context_acceptance.py`、`qwen_context_lifecycle.py` | 真实 AOQ 合成上下文/连续性/删除回执验收；无麦克风及电脑动作；可显式提供测试 WAV |
| `experiments/codex_reference_lifecycle.py`、`codex_product_session_acceptance.py` | 实际 Codex App Server 线程复用与临时参考移除验收；合成请求禁止工具，任务0步骤，独立临时目录；真实模型费用 |
| `smoke_pet.py` | `pet.py --smoke`，真实自然语言执行／语音检查；会操作应用和使用云端模型，`--dry-run` 仅将执行器替换为目标记录器，语音仍可调用云端 |
| `voice-demo.py`、`calculator.py` | 独立语音预实验；工具是本地延迟算术函数，不操作计算器 App |
| `probe-qwen-mlx.zsh` | 独立图片推理，非桌宠当前 worker |
| `codex_computer_use.py`、`computer_use.py`、`probe_codex_computer_use.py` | 历史 Python 接入与计算器实验；当前 Task Kernel 与 Computer Use Adapter 不导入它们 |
| `probe-computer-use.mjs`、`computer-use-mcp-client.mjs`、`verify-computer-use*.mjs` | 历史 Node 对照／真实计算器实验，不属于产品启动链路；Node/pnpm 用于 Electron 界面与 VRM 播放器 |
| `capture_mcp_initialize.py`、`diagnose_computer_use_transport.py`、`observe-computer-use.swift` | 历史握手、进程传输和前台状态诊断；不属于正常启动链路 |

`--smoke` 的 `--allow-app` 用于匹配测试收到的授权请求，不是隔离所有工具操作的系统沙箱；不要据此承诺模型无法访问其他应用。测试执行器明确关闭产品默认自动授权，让测试回调能够生效。原生真实验收也显式使用手动授权，用于检查授权 UI。

历史脚本保留用于复现技术选择与失败原因，不表示每条旧路径当前都可用。原始实验条件、失败路径见 `docs/` 中标注为历史的记录。新检查产物应放在 `.runtime/checks/`，私人材料放在 `.runtime/private/`；旧文档中的 `results/`、`logs/`、`.venv/` 与 `.venv-pet/` 不再作为当前安装路径。
