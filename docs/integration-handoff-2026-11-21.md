# BoxAgent 集成分支交接 · 2026-11-21

后续开发以 `codex/wechat-agent-integration` 为起点。本次功能与构建验收基线为 `3a63940`，已包含当时远端 `main` 的 `5247a42`，包括语音唤醒、Steam 进游戏提醒及日常语音闲置告别。分支交付的是代码、测试和文档，0.1.2 的 DMG 留在制作机器上做发包验收。

## 这次已实现

| 模块 | 已交付能力 | 从哪继续开发 |
| --- | --- | --- |
| LoL 陪玩 | 小窗或语音开启，使用本地对局事件主动点评。连杀按事件播报，高地与结束事件分别处理。英雄和装备资料使用 Data Dragon | [对局数据](../src/boxagent/infrastructure/games/lol.py) · [事件策略](../src/boxagent/domain/proactivity/lol.py) · [陪玩编排](../src/boxagent/application/lol_companion.py) · [资料查询](../src/boxagent/infrastructure/games/data_dragon.py) |
| 《鸣潮》陪玩 | 小窗显式开关。读取游戏窗口，支持任务进度、画面语境和短评的实验链路。可选云视觉 | [运行配置](setup.md#鸣潮陪玩预览) · [视觉适配](../src/boxagent/infrastructure/perception/wuwa.py) |
| 微信 | 本机接入后可读消息、监听、主动提醒和口述代回，能查看实际发送回执。群聊免打扰跳过主动通知，历史仍可查询 | [接入与使用](wechat-onboarding.md) · [事件接入](../src/boxagent/bootstrap/external_events.py) · [微信服务](../src/boxagent/application/wechat/service.py) |
| 语音调度 | 用户对话优先，过时的 LoL 短评跳过，微信等空档主动播报。游戏静音可截断当前短评，普通对话和微信沿用原语音设置 | [实时语音](../src/boxagent/infrastructure/runtimes/qwen/realtime.py) · [调度回归](../tests/test_speech_scheduling.py) |
| 桌宠与双屏 | 透明桌宠与无触屏副屏舞台可切换。角色气泡、脚下任务状态、聊天输入和任务灵动岛都已接通。游戏边缘半头像可展开关麦、静音短评与结束陪玩 | [显示说明](desktop-displays.md) · [桌面窗口](../web/panel/desktop/host.cjs) · [交互设计稿](design/companion/index.html) |
| macOS Onboarding 与发包 | 处理模型配置、组件安装、权限检查、微信接入和就绪状态。支持重开时恢复配置、从菜单进入修复以及独立 App／DMG 构建 | [安装与发包](macos-distribution.md) · [Onboarding 界面](../web/panel/src/onboarding/OnboardingPage.tsx) · [启动编排](../web/panel/startup/coordinator.cjs) |

## 同伴怎么跑

```sh
git clone --branch codex/wechat-agent-integration https://github.com/kono-dada/BoxAgent.git
cd BoxAgent
git lfs pull
```

| 要验证什么 | 入口与配置 |
| --- | --- |
| 日常源码开发 | 先按 [本机环境准备](setup.md) 装依赖和配置自己的模型 Key，使用 `./scripts/run-pet.sh` 启动。已有本机运行环境可用 `./scripts/run-local.sh` 复用模型和本机数据 |
| 改 Onboarding 或联调自己的微信 | 按 [微信源码接入](wechat-onboarding.md#同事从仓库本地运行2026-11-20) 准备固定微信与本地组件资源，再运行 `pnpm --dir web/panel setup`。接入后继续用同一入口和数据目录恢复本机加密配置 |
| 验证成品构建和首次安装 | 按 [macOS 安装与发包](macos-distribution.md) 准备构建资源，运行 `pnpm --dir web/panel dist:mac`。校验镜像，复制 App，推出镜像后运行成品冒烟 |

日常脚本与 Onboarding 可以复用模型和会话目录。微信凭据由各自的运行入口管理，普通 `run-local.sh` 当前不会读取 Onboarding 的加密凭据库。使用账号和麦克风联调时先退出另一实例。

## Onboarding 与微信验收

1. 打开独立 App 或源码的 `setup` 入口，填写自己的 DashScope Key、百炼 Workspace ID 和 DeepSeek Key。
2. 准备 core、Jev 和本地视觉组件，按需授予麦克风、录屏、辅助功能等权限。已有配置可以从「设置与权限…」重新检查。
3. 微信为可选步骤。进入微信页后，依次准备固定版本的微信副本、用自己的账号扫码、检查读取和监听，按需做后台发送预检，再启用微信。源码开发者先准备固定归档和随包运行资源。账号数据和密钥保存在各自机器。
4. 约一条测试来信，核对来信气泡与语音。由本人明确口述回复内容，发送后检查实际回执。后台发送预检只检查环境。

[微信使用、账号边界与故障排查](wechat-onboarding.md) · [macOS Onboarding、依赖与权限](macos-distribution.md)

## 验收到哪一步

- **已通过**　146 项桌面测试、45 项 VRM 测试、681 项 Python 测试，另有 13 项跳过。Pyright 无错误，保留一条既有 PyAudio 警告。0.1.2 成品通过摘要及严格签名校验，推出 DMG 后验证首次启动、离线组件安装、本地模型加载、重开后的 Engine 及微信副本准备。成品微信测试使用测试钥匙串。
- **有本机验证记录**　此前的微信读取、监听、通知和发送记录见微信模块设计。语音抢占有离线回归和合成来信设备回执。桌宠和双屏有本机实测及窗口回归。 [微信记录](wechat-模块设计.md) · [逐项实践记录](BoxAgent-实践记录.md)
- **待同伴复测**　新 Mac 上从扫码到冷启动恢复微信，真实来信、群免打扰和语音回执，LoL 实战中的短评及其他机器的独占全屏，鸣潮连续游玩时的识别和播报。正式分发还需 Developer ID、公证及干净机器的权限和启动验收。

## 后续改动记在哪里

- [README](../README.md) 介绍产品、演示和运行入口。
- [本机环境准备](setup.md)、[微信接入](wechat-onboarding.md) 与 [macOS 安装与发包](macos-distribution.md) 分别维护操作步骤。
- [实践记录](BoxAgent-实践记录.md) 按日期更新问题、决策、实现与实测口径。性能证据放在 `experiments/performance/`。源码结构见 [代码架构导览](BoxAgent-代码架构导览.md)，贡献和验证规则见 [AGENTS.md](../AGENTS.md)。
