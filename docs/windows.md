# Windows

需要 Python 3.12（由 uv 管理）、uv、Node.js、pnpm 和 Git LFS。
在仓库根目录使用 PowerShell：

```powershell
git lfs pull
./scripts/install.ps1
./scripts/run-pet.ps1
```

程序自动检测系统：macOS 使用 AppKit/Quartz 和 screencapture；Windows
使用 Win32 前台窗口 API 和 Pillow 的原生窗口截图，不改变窗口焦点。
Windows 不安装 PyObjC、AOQ 或 MLX；语音使用 WebSocket。

可选的本地窗口摘要需要单独安装视觉环境和原始 Transformers 模型
（不能使用 macOS 的 MLX 量化模型）：

```powershell
uv venv --python 3.12 .runtime/vision-venv
uv pip install --python .runtime/vision-venv/Scripts/python.exe torch 'transformers>=5.3,<6' 'pillow>=11.2.1,<13'
uv run --with huggingface-hub hf download Qwen/Qwen3.5-0.8B --local-dir .runtime/models/qwen3.5-0.8b
./scripts/run-pet.ps1 --context-interval 15
```

模型加载后只读取本地文件；有可用 CUDA 时使用 GPU，否则使用 CPU。
可用 `BOXAGENT_PERCEPTION_PYTHON` 和 `BOXAGENT_PERCEPTION_MODEL` 覆盖路径。
安装入口同时准备 `.runtime/jev-mem-venv` 的记忆服务依赖，也可配置
`BOXAGENT_JEV_MEM_PYTHON` 指向已有环境。

Windows 电脑操作默认复用 Codex 桌面应用的 **Control Windows apps / Computer Use**
技能，通过已注册的 `node_repl` MCP 和 `@oai/sky` 操作界面。
先在 Codex 桌面应用启用 Computer Use，并确认 `$CODEX_HOME/config.toml`
（默认 `%USERPROFILE%/.codex/config.toml`）中已配置可用的 `mcp_servers.node_repl`。
BoxAgent 只读取这项运行时配置，不修改用户的 Codex 全局配置；自身会话仍保存在独立目录。
已安装技能及其 API、操作限制、确认规则会一起提供给任务模型。

每次创建 Computer Use 任务运行环境时会只读检查原生通信管道：配置地址仍有效则保留；
旧地址失效且当前只有一个符合命名规则的管道时，仅更新本次任务的环境配置，
不改写 Codex 全局配置。没有管道或存在多个候选时停止并提示在 Codex 中刷新
Computer Use，避免连接错误实例。该逻辑不会启动原生 helper 或绕过应用授权。

执行链路：BoxAgent → Codex App Server → node_repl → @oai/sky → Windows API。
每次任务的工具返回仍进入审计与完成依据校验，结束或取消时通知 node_repl 清理当前轮次。
桌面应用需要保持运行，目标 Windows 桌面保持解锁；应用授权由 Computer Use 管理。
如需使用另装的兼容 MCP 执行器，可通过进程环境变量
`BOXAGENT_COMPUTER_USE_CLIENT` 指定可执行文件，覆盖默认技能路径。
