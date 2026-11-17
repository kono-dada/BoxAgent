# macOS onboarding 与打包验证记录

制作/验证机器：Apple Silicon，macOS 26.5.2。目标声明为 macOS 14+ arm64；没有据此推断其他系统版本已实机通过。

代码基线为 `main` 的 `2cafaaa`，实现分支为 `codex/onboarding-packaging`。MiraJelly 的参考分支已确认是 `release`，HEAD 为 `6876199`；没有复制其实现源代码。

## 产物

- `dist/mac/mac-arm64/BoxAgent.app`：Electron 38.8.6、CPython 3.12.9、随包 uv、core/Jev/vision wheel、官方 AOQ 1.3.0 framework。
- `dist/mac/BoxAgent-0.1.0-macos-arm64.dmg`：610,466,594 bytes；SHA256 为 `9e584c2385e9a22128d92083f631a7eecb9f76de3d2f35ad53260c30b81d390a`。本轮已重新制作，包含视觉修正与必填 DS 门禁。
- 默认 ad-hoc 签名，`codesign --verify --deep --strict`、2573 个资源 SHA256 校验及 `hdiutil verify` 通过。
- app、DMG、模型和运行数据保留在本机，源码分支不携带二进制。

## 自动化与成品检查

| 检查 | 结果与证据范围 |
| --- | --- |
| Python 全量测试 | 初次交付 455 passed，13 skipped，90 subtests passed；按 AGENTS.md 使用 Python 3.12 和 uv 执行。本轮视觉修订没有 Python 代码变化，保留这次验证的证据范围 |
| Pyright 1.1.414 | 初次交付仓库范围 0 errors、1 个现有 PyAudio source warning；额外检查新增/修改的 startup、settings、Electron bootstrap 与制作脚本，0 errors、0 warnings。本轮未改 Python 类型边界 |
| React/TypeScript/Vite | 构建通过；未提高 chunk warning 阈值或削弱类型检查 |
| Node 测试 | 本轮 67 passed；覆盖凭据、配置损坏、状态门禁、资源篡改、取消回收、修复保留数据、健康环境复用；缺失或无效 DeepSeek 阻止完成，旧已完成用户缺少 DS 返回配置页 |
| Electron UI fixture | 本轮使用真实 React 页面和 setup IPC，模拟 Provider/Engine；五页中英文与 200% 缩放、两组 Key 必填、回车提交、保存失败保留输入、Qwen/DS 鉴权失败、下载取消、权限状态、减少动画/透明度及高对比度；后台故障阻止完成引导。人工复核各页及三个输入框的焦点截图；首次密钥保护说明和标准尺寸权限刷新按钮可见性通过 |
| 移动后的实际 app | 本轮将最新 app 移至 checkout 外、路径含空格，设置只读；仅系统 PATH，使用独立数据目录；500×570 内容尺寸、居中且可见的主按钮、两组密码框与 DS 必填检查通过 |
| 实际独立运行环境 | 通过成品内的 Electron/uv/CPython 离线安装三个环境，不使用制作机器的 Python/Node/uv/Homebrew PATH |
| 实际本地模型 | 完整首次下载测试通过；固定 revision 的 MiniLM、SHA256 清单验证的 Qwen3.5-0.8B MLX 模型加载，真实 Jev backend 与 MLX 初始化通过，无云推理 |
| 实际后台 | 成品 Python desktop service 启动、Engine socket health 通过；没有模拟后台服务 |

成品检查入口为 `scripts/smoke-packaged-app.mjs`。首次完整下载测试使用全新目录；最后一轮复用此前测试下载的公开模型，新建依赖环境、Key 存储和状态，仍运行摘要/加载检查。Python 字节码缓存位于用户数据中，app 保持只读。

实测完整重复模型检查仍约 57.7 秒。因此日常重开不执行该深度检查，优先恢复必需凭据齐全的已配置用户桌宠；手动检查与首次完成门禁保留完整探测。本轮调整视觉、必填 DS 和返回用户启动编排后，另运行 Node、Electron UI、重新制作 app/DMG、成品签名/摘要和移动后的 UI 检查；未重复未改动的 wheel 安装和模型推理代码。模型与后台行保留初次交付的真实运行证据。

视觉参考、逐页人工复核和 Apple Design skill 取舍见 [视觉复核](macos-onboarding-visual.md)。Key 页面现在要求 API Key 与该 Key 对应的业务空间 ID，没有额外地域提示。

本机证据保存在 `.runtime/packaging-verification/`，包括制作、测试、成品检查日志和 Key 页面截图；文件不提交 Git。复现命令与配置见 [制作与安装说明](macos-distribution.md)。

## 尚未覆盖的发行验收

本机没有 Developer ID identity，未执行正式签名、公证、staple 或通过下载渠道的 Gatekeeper 验收。对应制作流程已经提供，不能将 ad-hoc 校验等同于正式发行。

没有使用有效用户 Key 测试云文字往返、AOQ 语音、Jev 云决策保存/召回，或实际 Computer Use 任务。权限查询与引导已实现，真实 TCC 授权、拒绝后恢复和正确执行者的权限仍需实机检查。独立目录与系统 PATH 检查不等于新 Mac 或干净 macOS 用户验收。

当前交付是可构建、可验证的开发分支与本机安装包；公开二进制发行、自动更新及 Windows 不包含在本轮源码推送中。
