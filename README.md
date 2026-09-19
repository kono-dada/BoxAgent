# BoxAgent 桌宠 POC

桌面小鸭、千问实时语音、字幕与状态表现，以及自然语言委托后台 Codex Agent 操作应用、反馈结果和取消任务。

本地屏幕总结已接入：默认每 15 秒读取前台应用最上层的普通窗口，截图最长边缩到 960 像素，交给本机 MLX Qwen3.5-0.8B。收起对话时，摘要在小鸭旁逐字浮现，速度随文本长度调整，约 2 秒内显示全文（受界面刷新调度影响），显示完至少保留 8 秒，长文本延长停留，悬停时不自动消失。气泡自动换行并按全文增高，超出半屏高度时可滚动查看，不再限制 3 行；展开对话时也可查看完整摘要、应用名和截图时间。气泡不抢焦点、不播报，也不会派发操作任务。模型不再被要求最多输出 40 字，生成预算提高到 512 token（仍保留防止异常长生成的上限）。

右键小鸭或菜单栏选择 **开启／关闭屏幕总结**。关闭会停止推理并释放模型进程；再次开启重新加载。启动参数可调整周期与分辨率：

```sh
uv run --script scripts/pet.py --log-dir ./logs/boxagent --context-interval 15 --context-size 960
```

`--context-interval 0` 表示启动时关闭，之后仍可从菜单开启；`--context-size 720` 可进一步降低图片分辨率。推理超过周期时不积压请求，切换前台窗口会丢弃旧窗口结果。截图只保存在推理期间的临时目录，完成、取消或失败后删除；摘要和模型诊断写入本机日志，不上传云端。需要启动终端的屏幕录制权限。已有 `.venv` 与 `models/qwen3.5-0.8b-mlx` 被直接复用，准备方法见 [本地 MLX 读屏](docs/qwen-mlx-probe.md)。

单次真实窗口检查：`uv run --script scripts/pet.py --check-context`。这是展示上下文感知的演示，0.8B 模型可能误读文字或概括不准，尚未作为任务执行或长期记忆的依据。

首次在新环境运行前，先按 [环境准备](docs/setup.md) 安装本机依赖、准备模型和官方执行器。仓库不包含模型、凭据或 Codex 二进制，也不保证克隆后无准备即可运行。

在项目根目录直接启动，不需要编译或生成应用包：

```sh
uv run --script scripts/pet.py
```

需要指定诊断目录时：

```sh
uv run --script scripts/pet.py --log-dir ./logs/boxagent
```

默认日志目录为 `.runtime/pet/`。`.runtime/` 和 `logs/` 均由 Git 忽略；选择其他目录时也应将其加入忽略规则。日志保留任务目标、工具参数与界面文字，可能包含私人信息，不应直接上传或提交。

默认自动允许 Computer Use 的操作授权请求，不再逐次弹出确认。需要恢复手动确认时，启动命令加上 `--require-approval`。这个设置不代替 macOS 的系统权限；系统授权仍需用户授予。

任务退出时，宿主会按本轮会话标识发送 `turn-ended` 通知，最多等待 8 秒，再退出执行器。`result.json` 的 `cursor_cleanup` 和事件日志记录通知状态；`notified` 仅表示客户端成功返回，不代表已经验证光标消失。该流程不重启共享服务，不清理其他会话。

也可以双击 `启动桌宠.command`，或使用 Codex 项目的 Run 按钮。uv 根据脚本依赖及 `scripts/pet.py.lock` 管理独立缓存环境，保留原来用于 MLX 的 `.venv`。首次运行需要下载依赖；PyAudio 依赖本机已有的 PortAudio。

- **Control + Option + 空格**：开启／关闭麦克风。初始麦克风关闭。
- **点击小鸭**：展开／收起对话；**拖动小鸭**：移动位置。
- **输入任务后按回车或点击箭头**：直接交给后台执行器，无需开启麦克风。运行期间可编辑下一条草稿；等待完成或停止当前任务后再提交。
- **右键小鸭或菜单栏 ◉**：打开控制菜单，或退出。终端 `Ctrl+C` 也可退出。
- 试说：“用计算器帮我算一下，503 加 219。”先听到回应，后台开始执行；等待时可以继续聊天。
- 也可以直接说：“帮我打开哔哩哔哩 App，然后随机点开播放一个视频。”前台把完整目标交给同一个通用 Agent，由它观察和选择操作。
- 后台动态发现 Computer Use 的应用列表、读界面、点击、输入、滚动等工具；代码不按应用分派，也没有场景模板或专用任务定义。具体能否完成取决于应用界面、工具能力和模型判断。
- 使用 `--require-approval` 时，工具请求授权后，气泡显示当前请求，可允许或拒绝。已允许的同一请求在本次进程内复用；默认自动允许不显示此按钮。
- 说“取消任务”或点“停止任务”，停止后续动作。普通插话只中断播报；关闭麦克风后后台任务继续，结果保留在气泡中。

建议戴耳机体验插话；当前没有客户端回声消除。首次开启麦克风时，macOS 可能要求允许启动它的终端或 Codex 访问麦克风；直接 uv 启动没有独立的 BoxAgent 权限身份。

程序读取已有 `.env.local` 中的 `DASHSCOPE_API_KEY`。尚未配置时运行 `zsh scripts/set-key.zsh`。语音默认沿用已验证的 `qwen-audio-3.0-realtime-plus`，切换方式：

```sh
BOXAGENT_VOICE_MODEL=qwen3.5-omni-flash-realtime uv run --script scripts/pet.py
```

后台沿用工作区已有 `.runtime/codex-0.153.0/` 的官方 Codex 和配套 `codex-code-mode-host`，模型默认 `gpt-5.6-luna`，使用已有 Codex 登录；也需要已安装的 Codex Computer Use 执行器。可用 `BOXAGENT_TASK_MODEL` 单独调整后台模型。具体已验证路径见 [Python Computer Use 记录](docs/python-codex-computer-use.md)。

**右键桌宠或点击菜单栏 ◉ →「形象商店…」即可换形象。** 可以搜索、翻页、查看分享者的来源页，点击「下载并使用」后立即切换；「已下载」中的形象支持离线使用，也可以随时「换回小鸭」。切换失败会保留原形象，成功后下次启动自动恢复。下载和切图在后台进行，切换保留桌宠位置、输入草稿和现有语音／任务生命周期。

形象来自 codex-pets.net，支持 V1/V2 图集。下载资源和选择保存在 `.runtime/pets/`，不随 `--log-dir` 改变；不会提交到 Git。分享者、来源与站点提供的许可信息随资源保存，未提供许可时不推断授权。内置 Debug Duck 的来源和 MIT 许可保存在 [assets/pet](assets/pet/README.md)。

本地兼容包仍可用 `--pet /绝对路径/角色目录` 加载，仅覆盖本次启动；未指定时依次使用上次有效选择、内置小鸭。完全不同的形象实现仍可通过 `Appearance` 接口接入。实现与验收方式见 [形象商店](docs/pet-store.md)。

运行记录保存在 `.runtime/pet/`：`events.jsonl` 是状态和字幕，`tasks/` 是操作、界面观察和结果；`position.json` 保存位置。此阶段没有长期记忆或时间线；除本地定时摘要外，操作应用时也会按需读取该应用的界面和截图。

指定 `--log-dir` 后，`events.jsonl`、`tasks/` 和 `context-worker.log` 改写到指定目录，窗口位置和单实例锁仍留在 `.runtime/pet/`。每个任务目录包含 `task.json`（目标、模型、开始时间）、`status.json`（最近心跳与执行阶段）、`events.jsonl`（RPC 耗时、动作、失败、异常堆栈及退出事件）、`agent-result.json`（模型原始结果）、`result.json`（成功、失败或取消的最终记录）及 `codex.log`。截图单独保存，不把 base64 写进事件日志。进程意外终止时可能没有最终记录；应结合心跳时间与进程状态判断，不能仅凭旧的 `running` 字段认为仍在运行。

个人电脑截图、界面文本及历史实验记录只保存在已被 Git 忽略的 `.runtime/private/`、`.runtime/pet/` 和 `results/`。`docs/assets/` 与旧 `artifacts/` 目录也加入忽略规则，避免再次误提交；角色素材仍保留在 `assets/pet/`。

并发与取消检查：

```sh
uv run --script scripts/pet.py --check
```

实际系统验收记录见 [POC 验收](docs/poc-verification.md)。原有独立语音 Demo 仍可通过 `uv run --script scripts/voice-demo.py` 运行；它使用本地延迟计算函数。相关预实验：[千问语音](docs/qwen-full-duplex-summary.md)、[本地 MLX 读屏](docs/qwen-mlx-probe.md)。

文档入口：[当前实现](docs/poc-implementation.md)、[按日期记录的验收](docs/poc-verification.md)、[历史规划](docs/poc-plan.md)、[脚本用途与副作用](scripts/README.md)、[首次提交检查](docs/first-commit-review.md)。历史预实验中的默认值与架构不代表当前产品。
