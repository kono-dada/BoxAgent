# 微信首次接入与自动副本准备

## 用户流程

用户不需要自己查找微信版本或下载安装包。BoxAgent 的发行资源包含经过构建号、腾讯签名和
ARM64 原生代码指纹检查的微信 **4.1.15 / build 270115**。首次接入：

1. 点击“准备微信副本”。宿主校验随包镜像的 SHA256 和大小，只读挂载后自动创建独立副本。
2. 点击“启动并扫码登录”。用户只在微信原生窗口用手机扫码，并完成需要的系统授权。
3. 程序在登录派生数据库密钥时取得本机读取凭据，逐库校验 HMAC，再检查读取及监听基线。
4. 可运行“后台发送预检”。保持副本主窗口打开即可，不激活窗口、不写文本、不实际发送。
5. 用户明确点击“启用微信”后，重启后台并接入既有 Inbox/Agent/通知链路。

引导说明：独立副本避免改动主微信安装及其本地数据，**不提供账号会话隔离保证**；
同账号登录副本可能让主微信下线，本机此前已发生过。
启用提醒后，相关消息及上下文会发给用户配置的模型。凭据只加密保存在本机，不进入渲染器或模型。

## 固定构建与自动资源

- 客户端必须同时匹配版本 4.1.15、build 270115、已验证的 ARM64 `Resources/wechat.dylib` SHA256。
- Qt vtable/RVA/元调用编号复用已验证配置；PID、ASLR 基址、实例和进程身份每次动态定位。
- 不支持时拒绝原生操作。不能用“同为 4.1.15”的热修构建替代，也不能自动回退最新版。
- `Frameworks/wechat.dylib` 是同名加载器，不是原生适配所针对的主库。
- 源归档保持腾讯签名，不含账号数据；副本在用户数据目录里创建并单独重签，原始归档不修改。
- 调试副本不嵌在 BoxAgent 的签名 App 层级中，而是在安装后创建。BoxAgent 自身不添加 get-task-allow。

构建机工具（不是用户安装步骤）：

```bash
uv run python -m boxagent.entrypoints.wechat_distribution \
  --source /path/to/verified/WeChat.app \
  --output .runtime/package-resources/wechat
```

`--source` 也可使用此前归档的固定 DMG，构建前仍只读挂载并验证签名及代码指纹。
产物为 `wechat/4.1.15.270115.dmg` 和 `wechat/manifest.json`。
`prepare-packaged-runtime.py` 已接入制作；`build-dist-mac.sh` 优先复用这份归档，
可用 `BOXAGENT_WECHAT_SOURCE` 指定构建机输入。输入不匹配时构建失败，不能交付缺微信资源的包。
资源进入发行包 SHA 清单，宿主首次准备时再次检查镜像摘要和内部 App 构建。

当前采用随包资源，避免用户依赖可变的腾讯 CDN 历史构建地址。以后可将同一固定归档放在下载服务，
宿主已有 HTTPS + 长度 + SHA256 校验路径；发布方必须填写已验证的固定摘要，不能填 latest 链接。
本机归档约 695 MB，发行包相应增大；未把它提交 Git，也未上传/发布。

## 分层和凭据边界

- `application/wechat/onboarding.py`：副本准备、取钥后验证的工作流及明确依赖协议。
- `bootstrap/wechat_onboarding.py`：组装现有数据库、快照、监听、原生发送适配器。
- `infrastructure/wechat/{replica,distribution}.py`：构建校验、源归档与用户副本创建。
- `infrastructure/wechat/database/capture.py`：只针对已验证副本的 KDF 捕获，原始 passphrase 不落盘。
- `entrypoints/wechat_setup.py`：宿主私有 JSON 管道协议，凭据不放命令行参数。
- `web/panel/startup/wechat.cjs`：资源获取、调用工作器、只返回公开状态，凭据交给现有 CredentialStore。
- `web/panel/startup/credentials.cjs`：使用 Electron safeStorage 加密保存数据库密钥映射；系统钥匙串保护密钥。
- 只有宿主从工作器取得密钥；通用 renderer `save` 不允许写微信内部凭据字段。
- Engine 使用受控环境注入凭据；打包环境不继承开发机的微信参数，也不回读源码 `.env.local`。

数据库密钥按当前账号目录与逐库 salt/HMAC 认证。更换账号/选另一账号目录需要重新获取，不能套用旧账号密钥。
首次取钥从指定副本可执行文件入口启动 LLDB，再等待扫码登录；不按模糊进程名附加，也不自动退出其他微信。
取消/超时只结束本次拥有的调试器，先请求 detach；没有全局 pkill、关闭 SIP 或绕过系统隐私授权。
检测到旧位置的 BoxAgent 副本仍运行时，同样要求用户先退出该副本，避免共享同一 bundle/data identity。

## 能力检查的准确含义

- 读取：认证联系人、会话和消息分片，实际执行查询。
- 监听：用独立临时状态跑真实数据库基线与空闲轮询，不改正式监听游标、不把旧消息投给 Agent。
  这只能证明数据库监听路径可运行；真实新消息回调仍需实机发入一条消息验收。
- 发送预检：用正式发送器的 `dry_run` 定位 filehelper、核对目标和空草稿。
  不代表实际发出消息，不代表任意未物化会话都可发送；已知的未物化会话限制仍保留。
- 实际发送仍走 DS 的“导航/身份校验 → 空草稿 → 写入/完整回读 → index8/action3 → 查库回执”路径。
  不确定结果不能自动重发；非空用户草稿不能覆盖。
- 微信失败或用户取消时恢复核心服务；未通过读取/监听校验不能启用提醒。

## 验证范围与剩余验收

2026-11-17 本机：

- 原版 App → 临时独立副本：真实复制、重签、严格签名及代码指纹校验通过，无 App 启动。
- 固定归档 → 宿主自动准备：8.051 秒，独立临时数据目录，不依赖用户本机微信，不登录、不发消息。
- 取钥执行器：独立 CommonCrypto 合成程序真实启动及条件捕获通过；7 秒延迟未触发原先错误的停止超时。
  该实验不读取真实微信密钥，也不等于新用户登录已验收。
- 用现有授权凭据运行新增读取与监听检查：通过，1.596 秒，无正文/联系人输出，无新登录。
- 引导六页 React/Electron fixture：中英文和 200% 缩放可跑；微信、模型、Engine 使用替身。

**尚未验收**：此版新流程真实扫码 → 获取 → 加密存储 → 冷启动恢复的完整链路，
干净 macOS 用户的文件访问/调试权限恢复，以及正式 Developer ID 签名、公证和最终整包安装。
当前依赖 Apple Command Line Tools 的 LLDB，新 Mac 没有时会提供系统安装入口；
不能把“辅助功能已授权”当成调试和数据库访问权限齐全。
原有微信读、监听及发送的本机成功记录继续见 `wechat-模块设计.md`，不冒充本轮新流程验收。

Apple 的调试器权限和 get-task-allow 是两侧独立要求：
[Debugging tool entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.debugger)。
发行包不应携带 get-task-allow：
[Resolving common notarization issues](https://developer.apple.com/documentation/security/resolving-common-notarization-issues)。
UI 采用逐阶段展开、说明数据用途及可取消等待，对照已安装 apple-design 的
`onboarding.md › Best practices` 和 `privacy.md › Protecting data`；不据此声称发行验收已通过。

## 2026-11-17：本机安装测试包已制作

- 产物：`dist/mac/BoxAgent-0.1.0-macos-arm64.dmg`，1,309,350,952 bytes。
- SHA256：`94f98556f1bb2f81e8c5e3e3cf9ca5ec8e5a6baf2c1a040d93165320adc6fe45`。
- 实际准备了 CPython 3.12.9、core/Jev/vision 原生 wheel、AOQ SDK 和固定微信归档。
- 2625 个资源摘要与微信固定构建清单检查通过；App 严格签名校验、DMG CRC 校验通过。
- 实际 App 复制至含空格的独立临时路径，设为只读、只用系统 PATH，首次引导及必填 Key 表单启动通过。
- 本机没有 Developer ID identity，本包为 ad-hoc 签名，未公证，不等同于正式发行完成。
- 按用户要求直接交付本地安装试用，尚未执行此安装包的真实扫码、凭据冷启动恢复或完整模型准备验收。

制作日志、校验及启动记录在 `.runtime/private/artifacts/wechat-onboarding-acceptance-20261117/`。
临时启动验收没有配置用户 Key、登录微信、发送消息或修改既有安装。

## 2026-11-17：实际安装后的启动问题与修复

- 从旧 DMG 直接运行后，venv 仍引用旧卷的 Python；推出旧卷后返回 `process_start_failed`。已在本机通过组件页离线修复到 `/Applications/BoxAgent.app`，源码增加来源路径检查与微信操作前 core 复查。
- 自动准备的副本最初不能启动。AMFI 实际记录 `-424: The file is adhoc signed but contains restricted entitlements`；LLDB 返回 `error: process exited with status -1 (no such process)`，旧代码却将进程退出归为 `wechat_capture_timeout`。
- 副本重签去掉了不适用于本地临时签名的 `com.apple.developer.team-identifier`。保留 App Sandbox、独立 Bundle ID 和独立组名；旧副本可在重新准备时修复，重复执行不会叠加组名后缀。未修改主微信、系统安全策略或聊天数据库。
- 本机修复后的副本真实启动进入聊天窗口，BoxAgent 的读取、监听检查均显示已就绪，用户确认配置成功。该观察不代替全新 macOS 用户的首次扫码及凭据冷启动验收，也没有执行发送测试。
- 源码将启动失败、进程退出与真正的等待登录超时分开提示。当前已安装副本已修复；新的 Python 和 UI 防护尚未重新制作进 DMG。
- 回归：项目及微信实验 576 passed、13 skipped、90 subtests passed；Pyright 0 errors，保留既有 PyAudio source warning。根目录全量 pytest 在第三方 PersonOS、rea、eciyuan/Ghidra 的 8 项缺失依赖收集错误处停止。
