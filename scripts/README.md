# 脚本用途

桌宠运行代码位于 `boxagent/`。本目录同时保留入口、验收工具和历史预实验；计算器相关规则只属于独立实验，不是当前桌宠的任务定义。

| 脚本 | 用途与副作用 |
| --- | --- |
| `pet.py`、`pet.py.lock` | 唯一产品入口与宿主依赖锁，`uv run --script scripts/pet.py` |
| `window_summary_worker.py` | 当前产品依赖的常驻 MLX 子进程，由观察适配器启动 |
| `set-key.zsh` | 交互写入本地语音密钥，不提交输出 |
| `download-qwen-mlx.zsh` | 下载模型至忽略目录，需网络、jq 与磁盘空间 |
| `check_pet_ui.py` | `pet.py --check-ui`，仅测试窗口、合成文字和替身；结果在 `.runtime/pet/ui-check/` |
| `check_pet_store.py` | `pet.py --check-pets-ui`，访问真实商店并下载 V1/V2 素材，显示原生窗口；语音与任务使用替身，不录音、不操作其他应用，缓存与截图隔离在 `.runtime/pet-store-check/` |
| `check_window_summary.py` | `pet.py --check-context`，读取真实前台窗口，运行本地模型 |
| `smoke_pet.py` | `pet.py --smoke`，真实自然语言执行／语音检查；会操作应用和使用云端模型，`--dry-run` 仅将执行器替换为目标记录器，语音仍可调用云端 |
| `smoke_desktop.py` | `pet.py --smoke-desktop --audio /绝对路径/请求.wav`，真实语音、授权界面与桌面操作检查；显式要求 PCM16、16 kHz、单声道 WAV，不再依赖未提交的固定录音 |
| `voice-demo.py`、`calculator.py` | 独立语音预实验；工具是本地延迟算术函数，不操作计算器 App |
| `probe-qwen-mlx.zsh` | 独立图片推理，非桌宠当前 worker |
| `codex_computer_use.py`、`computer_use.py`、`probe_codex_computer_use.py` | 历史 Python 接入与计算器实验；当前 `boxagent/executor.py` 不导入它们 |
| `probe-computer-use.mjs`、`computer-use-mcp-client.mjs`、`verify-computer-use*.mjs` | 历史 Node 对照／真实计算器实验，不是产品依赖，无需为启动桌宠安装 Node |
| `capture_mcp_initialize.py`、`diagnose_computer_use_transport.py`、`observe-computer-use.swift` | 历史握手、进程传输和前台状态诊断；不属于正常启动链路 |

`--smoke` 的 `--allow-app` 用于匹配测试收到的授权请求，不是隔离所有工具操作的系统沙箱；不要据此承诺模型无法访问其他应用。测试执行器明确关闭产品默认自动授权，让测试回调能够生效。原生真实验收也显式使用手动授权，用于检查授权 UI。

历史脚本保留用于复现技术选择与失败原因，不表示每条旧路径当前都可用。原始实验条件、失败路径见 `docs/` 中标注为历史的记录。原始界面、截图、日志、音频仅留在 `.runtime/`、`results/`、`logs/` 等忽略目录。
