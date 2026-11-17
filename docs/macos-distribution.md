# macOS 首次引导与安装包

首版面向 Apple Silicon macOS 14 及以上。应用使用固定 Python 3.12.9；用户不需要安装 Python、uv、Node、pnpm 或 Homebrew。需要联网配置云模型和准备记忆、视觉模型。声明的最低系统版本不等于整个系统版本矩阵已实机验收。

## 新用户

将 `BoxAgent.app` 从 DMG 拖到 Applications 后打开。首次引导依次显示欢迎、模型服务、本地组件、权限和就绪摘要。

- 填写自己的 DashScope API Key 和北京地域百炼 Workspace ID。默认记忆决策复用这组配置。
- DeepSeek API Key 用于桌面任务，可以稍后填写。Key 保存后执行受限验证，错误时保留配置并提示重试。
- 首次准备 core、Jev-Mem、MLX 环境及记忆/视觉模型。环境依赖来自随包 wheel，模型准备需要网络。建议预留至少 6 GB。
- 麦克风、辅助功能、屏幕录制按实际用途授权。可以先用文字；摄像头在首次开启时单独授权。
- 只有 core、Engine、Qwen、真实 Jev-Mem 和默认视觉组件通过最终检查后才记录首次完成。

桌面任务还需要官方 Codex、同版本 `codex-code-mode-host` 以及 Computer Use。当前安装包不重新分发这些官方组件；引导提供官方发布页和组件目录选择。选择 Codex 时指定含两个可执行文件的目录；选择 Computer Use 时指定外层 `Codex Computer Use.app`。如果官方组件尚未安装，文字聊天仍可使用，桌面执行能力显示受限。这不等于完整桌面任务已验收。

正常重开恢复桌宠，组件准备完成的环境继续复用。通过控制台「设置 → 配置与修复」重新打开引导，可以修改/删除 Key、重新验证、检查权限或修复组件。留空的 Key 输入框保留已保存值；明确删除按钮会删除对应 Key。

Key 通过 Electron `safeStorage` 加密，macOS Keychain 保护其加密密钥；API Key 密文本体保存在用户目录。这与直接将每个 API Key 存为一个 Keychain item 不同。引导状态只保存步骤与完成时间；Renderer 无读取完整 Key 的接口。

用户数据位于 `~/Library/Application Support/BoxAgent`，包含状态、隔离运行环境、模型、会话、记忆和日志。`BOXAGENT_DATA_DIR` 可以指定独立目录。应用资源只读，修复依赖不删除会话或记忆。

Python 字节码缓存通过 `PYTHONPYCACHEPREFIX` 写到用户的 `runtimes/pycache`，避免每次健康检查重新编译重型依赖，且不写入 app 资源。

已完成引导的用户重开时检查 core 和 Engine 后恢复桌宠，不阻塞等待重型模型重复初始化。首次完成、明确的重新检查/修复仍运行完整本地模型检查；跳过深度检查不被报告为模型已就绪。

## 制作机器

安装 Node、pnpm 10.33.2、uv、PortAudio、Xcode Command Line Tools、Git LFS；准备官方 AOQ 1.3.0 的 `AoqClientSdk.framework` 与 `PluginOpus.framework`。制作依赖使用现有版本锁，禁止将开发 venv、`.env.local` 或个人 Codex auth 复制进包。

```sh
git lfs pull
pnpm --dir web/vrm install --frozen-lockfile
pnpm --dir web/panel install --frozen-lockfile
export BOXAGENT_AOQ_SDK_DIR=/absolute/path/to/frameworks
pnpm --dir web/panel dist:mac
```

入口构建 VRM 和 React，准备可迁移 CPython、三个隔离环境的 wheel 和摘要，修复非系统动态库路径，检查原生代码，制作 app 与 DMG。制作缓存位于 `.runtime/package-cache`，按输入锁、制作配方、Python 和签名身份隔离；完整摘要验证后才复用。`input-wheels.json` 保存制作前 wheel 摘要；由源代码构建的 PyAudio wheel 属于制作输入，不冒充上游原始 wheel。

产物位于 `dist/mac/`。测试默认 ad-hoc 签名，**不是 Developer ID 签名公证发行包**。源码变化、依赖未变化时可复用已检查的制作资源：

```sh
bash scripts/build-dist-mac.sh --reuse-resources
```

正式签名需要自己的 Developer ID 和已配置的 notarytool Keychain profile：

```sh
export BOXAGENT_SIGN_IDENTITY='Developer ID Application: Your Team (TEAMID)'
export BOXAGENT_NOTARY_PROFILE='BoxAgent-Notary'
pnpm --dir web/panel dist:mac
```

正式模式签名原生 wheel、Python、Framework 和 Electron，更新实际资源摘要，然后公证 app 和 DMG、staple 并检查 Gatekeeper。任何一步失败停止制作。签名身份变化必须重新准备 wheel，不能在 `--reuse-resources` 时假设旧 wheel 已符合新身份。

## 验证

```sh
uvx --from pyright==1.1.414 pyright
UV_CACHE_DIR=/tmp/uvcache uv run --with pytest python -m pytest -q
pnpm --dir web/panel build
pnpm --dir web/panel test
web/panel/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron web/panel/tests/onboarding-smoke.cjs
node scripts/verify-packaged-app.mjs dist/mac/mac-arm64/BoxAgent.app
node scripts/smoke-packaged-app.mjs dist/mac/mac-arm64/BoxAgent.app --models
```

Electron smoke 使用隔离目录和模拟 Provider/Engine，验证真实页面、Key 表单、中英文、Engine 故障和完成门禁，不调用真实模型或操作其他应用。截图写入 `.runtime/onboarding-smoke/screenshots`。生产代码中没有模拟 Provider 的开关。

成品另须在无源码、无开发工具 PATH 的隔离用户目录启动，并运行随包 core 的离线安装/健康检查。模拟边界通过不代表真实 Key、云对话、AOQ、记忆决策、TCC 或 Computer Use 已验收。最终验证记录分别列出结果与未覆盖项。

`smoke-packaged-app.mjs` 将 app 复制到制作机器的临时目录（含空格），仅保留系统 PATH，以实际成品打开 Key 表单，使用成品内的 Electron、uv 和 CPython 离线安装三个环境，并检查真实 Engine。`--models` 另下载模型并初始化真实 Jev/MLX；合成配置只用于本地 Jev 初始化，不进行云推理。此检查保留临时用户目录和截图以便诊断，需要图形会话，不能代替干净机器与真实凭据验收。

重复制作检查可添加 `--model-cache "/path/to/previous-test/User Data/models"`，复用之前测试下载的公开模型文件；依赖环境、状态和 Key 存储仍从新的隔离目录开始，模型继续执行摘要与加载检查。

随包 `third-party-notices` 提供实际 Python wheel 的许可文本、版本清单，以及 Electron/Chromium、uv 和被打入音频 wheel 的 PortAudio notices。AOQ 只复制两个官方 framework，忽略 ZIP 的 `__MACOSX` 元数据。重型环境明确选择 macOS 14 wheel；MLX 与相同版本的 `mlx-metal` 保留上游共享命名空间布局，由实际环境加载检查验证其依赖。

Git 分支发布只包含源码、锁、配置和文档；wheel、模型、SDK、用户数据、Key 和二进制产物不提交 Git。二进制外部分发和自动更新渠道是独立发布动作。

本次实际结果与未覆盖项见 [2026-11-17 验证记录](macos-validation-2026-11-17.md)。
