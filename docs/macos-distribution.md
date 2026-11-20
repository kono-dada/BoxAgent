# macOS Onboarding 与安装包

首版面向 Apple Silicon macOS 14 及以上。应用使用固定 Python 3.12.9；用户不需要安装 Python、uv、Node、pnpm 或 Homebrew。需要联网配置云模型和准备记忆、视觉模型。声明的最低系统版本不等于整个系统版本矩阵已实机验收。

本轮成品为本机验证发包流程生成的 0.1.2 测试包。源码运行见 [本机环境准备](setup.md)，已交付功能和同伴验收清单见 [2026-11-21 集成交接](integration-handoff-2026-11-21.md)。

## 新用户

打开 DMG 后，按窗口中箭头把左侧 `BoxAgent.app` 拖到右侧 Applications；复制完成后从“应用程序”打开 BoxAgent。
拖拽复制本身不会自动启动应用。Onboarding 依次显示欢迎、模型服务、本地组件、权限、微信和就绪摘要。

- 填写自己的 DashScope API Key 和该 Key 对应的百炼业务空间 ID。默认记忆决策复用这组配置。
- DeepSeek API Key 用于桌面任务，首版必填。两组 Key 保存后执行受限鉴权验证，错误时保留配置并提示重试。
- 首次准备 core、Jev-Mem、MLX 环境及记忆/视觉模型。环境依赖来自随包 wheel，模型准备需要网络。建议预留至少 6 GB。
- 麦克风、辅助功能、屏幕录制按实际用途授权。可以先用文字；摄像头在首次开启时单独授权。
- 微信是可选项。要启用时，在微信页准备固定版本的副本、扫码、检查读取与监听，再开启来信提醒。如何口述代回和核对发送结果见 [微信使用说明](wechat-onboarding.md#接入后怎么用)。
- 只有 core、Engine、Qwen、DeepSeek、真实 Jev-Mem 和默认视觉组件通过最终检查后才记录首次完成。

桌面任务还需要官方 Codex、同版本 `codex-code-mode-host` 以及 Computer Use。当前安装包不重新分发这些官方组件；引导提供官方发布页和组件目录选择。选择 Codex 时指定含两个可执行文件的目录；选择 Computer Use 时指定外层 `Codex Computer Use.app`。如果官方组件尚未安装，文字聊天仍可使用，桌面执行能力显示受限。这不等于完整桌面任务已验收。

正常重开恢复桌宠，组件准备完成的环境继续复用。通过控制台「设置 → 配置与修复」重新打开引导，可以修改/删除 Key、重新验证、检查权限或修复组件。留空的 Key 输入框保留已保存值；明确删除按钮会删除对应 Key。

升级替换 App 不会删除 `~/Library/Application Support/BoxAgent` 中的配置、模型、微信凭据和引导状态。已完成引导直接启动；未完成则恢复上次步骤。组件输入摘要和 App 路径不变时复用已通过检查的环境；从 DMG 搬到 Applications 等路径变化可能需要点击「准备或修复组件」，从安装包内的 wheel 离线重建环境。已有且校验通过的视觉模型不再下载，已有记忆模型通过本地检查后跳过下载准备；缺失或损坏的模型才需补齐。取消后留下的未完成单文件不保证断点续传。

运行环境检查会比对 marker 中的 Python 来源路径和当前 App 的资源路径。`/var` 和 `/private/var` 等指向同一解释器的路径会通过检查。来源变化时返回 `component_runtime_relocated`，即使旧 DMG 尚在挂载也会要求修复。微信操作前单独复查 core，失败返回 `wechat_runtime_unavailable` 并提供「修复本地组件」入口。升级验收覆盖「从 DMG 运行过 → 拖到 Applications → 推出旧卷 → 修复组件 → 准备微信」。

录屏按钮先由 BoxAgent 本身调用 Electron `desktopCapturer.getSources` 请求权限，再按实际状态打开系统设置；仅打开设置不会使 App 出现在录屏列表。此次请求关闭缩略图且不保存帧。授权后系统可能要求重启 App；Computer Use 是另一个执行程序，可能另有权限条目。测试包采用 ad-hoc 签名，不能承诺跨构建沿用全部系统权限；正式升级应保持 Developer ID、Bundle ID 和安装位置稳定。

桌面组件自动检测本机 App Server 插件目录、仓库运行环境和 Codex.app，并把解析后的可执行路径传给 Engine，不把探测路径固化到用户配置。DeepSeek API 负责模型推理，App Server 与 Computer Use 负责本地执行；模型 Key 不会安装这些二进制文件。手动选择组件仅放在故障排查中。当前安装包仍不随包提供这两项，干净机器的自动获取流程另行完善。

Key 通过 Electron `safeStorage` 加密，macOS Keychain 保护其加密密钥；API Key 密文本体保存在用户目录。这与直接将每个 API Key 存为一个 Keychain item 不同。引导状态只保存步骤与完成时间；Renderer 无读取完整 Key 的接口。

用户数据位于 `~/Library/Application Support/BoxAgent`，包含状态、隔离运行环境、模型、会话、记忆和日志。`BOXAGENT_DATA_DIR` 可以指定独立目录。应用资源只读，修复依赖不删除会话或记忆。

Python 字节码缓存通过 `PYTHONPYCACHEPREFIX` 写到用户的 `runtimes/pycache`，避免每次健康检查重新编译重型依赖，且不写入 app 资源。

已完成引导且必需凭据齐全的用户重开时检查 core 和 Engine 后恢复桌宠，不阻塞等待重型模型重复初始化。旧测试数据如果缺少 DeepSeek，会回到 Key 配置页并保留已有数据。首次完成、明确的重新检查/修复仍运行完整本地模型检查；跳过深度检查不被报告为模型已就绪。

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

产物默认位于 `dist/mac/`，安装镜像为 `BoxAgent-<version>-macos-arm64-setup.dmg`。安装窗口由 `scripts/build-dmg-mac.cjs` 复用已签名 App 制作，写入 Finder 图标坐标、600×400 窗口和 Retina 拖拽说明背景。同名 `BoxAgent Installer` 已挂载时会停止制作。

制作同版本的多个测试候选时，可通过 `BOXAGENT_PACKAGE_OUTPUT_DIR` 指定独立产物目录。App 的中间产物仍共用 `.runtime/package-build/mac-arm64/BoxAgent.app`。依赖未变化时，`--reuse-resources` 复用已检查的 Python、wheel 和 AOQ 制作资源，VRM 和 Electron 前端仍会重新构建。以下命令使用 0.1.2 的文件名。

```sh
BOXAGENT_PACKAGE_OUTPUT_DIR="$PWD/dist/mac/companion-check" \
  bash scripts/build-dist-mac.sh --reuse-resources
cd dist/mac/companion-check
shasum -a 256 -c BoxAgent-0.1.2-macos-arm64-setup.dmg.sha256
hdiutil verify BoxAgent-0.1.2-macos-arm64-setup.dmg
```

测试默认使用 ad-hoc 签名。正式分发走下方 Developer ID 签名与公证流程。

升级测试包时退出旧实例，将新 App 覆盖到同一个 `/Applications/BoxAgent.app`，再从 Applications 打开。引导进度与加密凭据位于用户数据目录，替换 App 不清除它们；macOS 权限由系统另外维护，不由引导完成标记推断。临时签名变动后需要以当前 App 的实际权限检查为准，不能承诺每次构建自动继承旧授权。

0.1.2 起，启动检查会比较当前代码签名的 designated requirement 与上次完成权限检查的身份。已完成引导的用户遇到新身份且权限缺失时，会直接进入权限修复页，保留原有 Key、模型与微信配置。用户可逐项请求授权、重启检查，或明确选择稍后授权继续；同一身份的明确跳过会保存，状态仍如实显示未授权。授权页重新获得焦点时只刷新实际状态，不自动申请权限。菜单「设置与权限…」（⌘,）可随时重新进入，不需要操作人物或唤醒。此流程只处理重新授权的引导，不能代替稳定的 Developer ID 签名，也不重置整个 TCC 数据库。

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
node scripts/verify-packaged-app.mjs .runtime/package-build/mac-arm64/BoxAgent.app
node scripts/smoke-packaged-app.mjs .runtime/package-build/mac-arm64/BoxAgent.app --models
```

Electron smoke 使用隔离目录和模拟 Provider/Engine，验证五个真实页面、中英文、200% 缩放、Key 必填、保存失败保留输入、两种服务的鉴权失败、下载取消、权限状态以及完成门禁。截图写入 `.runtime/onboarding-smoke/screenshots`。生产代码中没有模拟 Provider 的开关。视觉参考和取舍见 [视觉复核](macos-onboarding-visual.md)。

成品另须在无源码、无开发工具 PATH 的隔离用户目录启动，并运行随包 core 的离线安装/健康检查。模拟边界通过不代表真实 Key、云对话、AOQ、记忆决策、TCC 或 Computer Use 已验收。最终验证记录分别列出结果与未覆盖项。

`smoke-packaged-app.mjs` 将 app 复制到制作机器的临时目录（含空格），仅保留系统 PATH，以实际成品打开 Key 表单，使用成品内的 Electron、uv 和 CPython 离线安装三个环境，并检查真实 Engine。`--models` 另下载模型并初始化真实 Jev/MLX；合成配置只用于本地 Jev 初始化，不进行云推理。此检查保留临时用户目录和截图以便诊断，需要图形会话，不能代替干净机器与真实凭据验收。

验收最终 DMG 时，先校验摘要并挂载镜像，确认安装窗口有 App 和 Applications 快捷方式。把 App 复制到临时安装目录，推出镜像，再对复制件运行 `codesign --verify --deep --strict` 和上述两个 Node 脚本。这样可以核对实际交付文件，以及离开镜像后启动本地组件和 Engine 的能力。

重复制作检查可添加 `--model-cache "/path/to/previous-test/User Data/models"`，复用之前测试下载的公开模型文件。依赖环境、状态和 Key 存储仍从新的隔离目录开始，模型继续执行摘要与加载检查。

在已安装 BoxAgent 的机器上测试新 ad-hoc 签名时，系统钥匙串可能等待用户授权。自动冒烟可加 `--mock-keychain`，仅在隔离目录用 Chromium 测试钥匙串验证准备流程。真实钥匙串和微信登录验收需要单独运行，`--wechat-login` 禁止与此参数并用。 [Electron 签名说明](https://www.electronjs.org/docs/latest/tutorial/code-signing#macos-apis-that-require-code-signing)

随包 `third-party-notices` 提供实际 Python wheel 的许可文本、版本清单，以及 Electron/Chromium、uv 和被打入音频 wheel 的 PortAudio notices。AOQ 只复制两个官方 framework，忽略 ZIP 的 `__MACOSX` 元数据。重型环境明确选择 macOS 14 wheel；MLX 与相同版本的 `mlx-metal` 保留上游共享命名空间布局，由实际环境加载检查验证其依赖。

Git 分支发布只包含源码、锁、配置和文档；wheel、模型、SDK、用户数据、Key 和二进制产物不提交 Git。二进制外部分发和自动更新渠道是独立发布动作。

陪玩版的验收见 [实践记录](BoxAgent-实践记录.md#2026-11-21--陪玩版-macos-安装验收)，此前的引导测试见 [2026-11-17 验证记录](macos-validation-2026-11-17.md)。
