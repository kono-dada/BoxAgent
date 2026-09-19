# 本机环境准备

这是 macOS / Apple Silicon 的本机 POC，不是开箱即用的分发包。以下与 2026-09-19 的代码路径对应；已在现有机器运行，不代表已完成全新机器安装验收。

## 桌宠宿主与语音

准备 uv、Python 3.12 和 PortAudio。使用 Homebrew 的机器可以安装 `uv`、`portaudio`；PyAudio 首次构建还可能需要 Xcode Command Line Tools。桌宠的 Python 依赖由 `scripts/pet.py` 与 `scripts/pet.py.lock` 管理，不要把它们装入视觉 `.venv`。

在项目根目录运行：

```sh
zsh scripts/set-key.zsh
uv run --script scripts/pet.py --log-dir ./logs/boxagent
```

密钥用于北京地域 DashScope，脚本写入忽略的 `.env.local`。也可通过环境变量 `DASHSCOPE_API_KEY` 提供。没有语音密钥时仍能打开桌宠、提交文字任务；开启麦克风才会读取密钥。

优先从独立 Terminal 启动。直接 uv 运行没有独立应用权限身份；系统权限取决于启动终端及 Computer Use 组件。麦克风用于语音，屏幕录制用于本地截图；电脑操作还依赖执行组件获得所需系统权限。产品内的自动／手动授权不是 macOS 权限开关。

## 本地窗口摘要

按 [MLX 预实验](qwen-mlx-probe.md) 的命令准备 `.venv` 和 `models/qwen3.5-0.8b-mlx/`。下载脚本使用 `curl`、`jq`、`shasum`；模型来自远端 master，下载时校验远端提供的哈希，但没有固定模型仓库 revision，不能保证未来下载与历史模型逐字节相同。

当前宿主默认开启观察；环境缺失时会暂停观察并显示错误，不阻止文字任务。暂时不准备视觉环境可使用：

```sh
uv run --script scripts/pet.py --log-dir ./logs/boxagent --context-interval 0
```

MLX 依赖目前记录了核心版本，但没有独立的完整依赖锁文件；全新环境的完全可复现性仍待验证。

## 电脑操作执行器

当前代码固定使用项目内 `.runtime/codex-0.153.0/`，不会自动使用 PATH 中的新版本。准备方式：

1. 从 [官方 0.153.0 发布页](https://github.com/openai/codex/releases/tag/rust-v0.153.0) 获取 Apple Silicon macOS 的 Codex 和同版本 `codex-code-mode-host` 压缩包。
2. 解压后将两个可执行文件放为 `.runtime/codex-0.153.0/codex`、`.runtime/codex-0.153.0/codex-code-mode-host`，保留执行权限；确认来源和签名，不修改签名或绕过系统校验。
3. 通过官方 Codex 客户端完成登录，并安装其 Computer Use 组件。程序读取 `${CODEX_HOME:-$HOME/.codex}/computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient`。

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
