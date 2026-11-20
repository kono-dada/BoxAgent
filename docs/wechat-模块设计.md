# WeChat 接入模块设计（BoxAgent）

## 2026-11-17：首次接入补充

已增加自动副本资源、扫码取钥工作流、加密凭据保存和引导页面。
用户无需自行下载微信，固定 4.1.15 / build 270115 的源归档由发行资源提供，
安装后由程序创建副本。自动副本准备实测通过；本轮新流程的真实扫码及正式签名安装验收仍未完成。
详见 [微信首次接入与自动副本准备](wechat-onboarding.md)。下文保留先前实验及核心模块验收记录。

## 2026-11-16：本机模块实现

按本轮确定的分层迁入了读取、监听和原生发送，继续使用本机已有派生密钥。
首次获取密钥和 onboarding 本轮不实现。朋友圈优先级后移，保留实验适配器，
工具入口默认不注册朋友圈功能。通用 Inbox / Agent / 通知接线已实现；
2026-11-17 用户明确授权微信数据交给已登录的 Codex/OpenAI 模型处理后，
本机监听已开启，并通过真实新消息的完整链路验收，见下一节。

新增入口：

- `application/wechat/module.py`：`WeChatModule`，统一读写与监听入口。
- `application/wechat/service.py`：稳定目标解析、重名候选、持久请求编号、发送回执。
- `application/wechat/monitor.py`：后台生命周期及向 EventSink 的持久交接。
- `domain/events/`：通用 `EventEnvelope` / `EventReceipt` / 异步 `EventSink` 协议。
- `infrastructure/wechat/database/`：加载并认证既有密钥、快照和解密、查询。
- `infrastructure/wechat/listener.py`：变化检测、每库每表游标、事务 outbox、事件投递。
- `entrypoints/wechat.py`：独立 CLI。
- `agent/harness/wechat_tools.py`：只依赖领域协议的工具适配器，启用模块时自动注册到现有执行器。

### 2026-11-16：接入 Agent 与通用 Inbox

链路为 `WeChatMonitor → EventRuns.accept → SQLiteEventInbox → ExternalEventContext →
CodexEventAgent → NotificationOutbox → 现有桌面/语音通知通道`。
`bootstrap/external_events.py` 负责构造依赖；应用统一管理监听、事件工作器和运行时的生命周期。
后续邮箱只需实现 EventSink 的生产端和可选的来源上下文提供器，共用 Inbox 和消费链路。

- Inbox 用 `(source, account_id, event_id)` 去重；持久提交后才确认源 outbox。
- 同一来源/账号/会话最多10条为一批，等待750ms合并邻近消息。120秒租约、15秒续租；
  重启恢复相同 run_id。决策在通知前缓存，通知以 run_id 去重，失败5秒后重试。
- 两分钟内发出的实时消息优先于停机补收，即使同一会话存在旧消息也先处理实时批次。
  旧消息仍持久保存，空闲时按原顺序补收；不以删除或重置游标来清积压。
  参考上下文附带消息年龄与采集延迟，Agent 不把补收的旧询问当成刚发生。
- 事件使用独立的短 Agent 轮次，复用人格、Product Session 历史、环境和记忆投影。
  外部消息在 evidence 中作为数据，不调用 begin_interaction，不伪装成用户发言。
- 独立 Codex Host 不启动 CU；关闭 shell、apps、multi_agent、web search 等能力，
  以 read-only/never 和空动态工具运行；该轮只返回 notify/silent、摘要、来源ID。
  来源引用必须属于当前批次，超长/无来源提醒会拒绝并保留事件。
- 只有实际通知确认后才写入 assistant 历史，保留 source/account/subject/sender/event 引用。
  后续用户说“回他”能依据引用定位；聊天正文和引用本身不授予发送权限。
- 用户主动任务的 wechat_* 工具复用现有观察步骤与证据记录；发送仍走原生序列和持久请求账本。
  写工具可用不代表允许自行回复；实际发送需用户指令且显式 dry_run=false。
- 没有语音凭据时文字任务和桌面通知仍可运行。语音接线复用既有通道；
  后续补齐配置与 SDK 后已完成固定合成句子的原生实播，见本节末尾。

配置 `.env.local` 或同名环境变量（默认关闭；切换后重启 Engine）：

```dotenv
BOXAGENT_WECHAT_ENABLED=1
BOXAGENT_TASK_PROVIDER=codex
# BOXAGENT_WECHAT_APP=/path/to/replica.app
# BOXAGENT_WECHAT_KEYS=/private/path/derived-keys.json
# BOXAGENT_WECHAT_DB_ROOT=/private/path/db_storage
# BOXAGENT_WECHAT_POLL_INTERVAL=0.5
```

本机现有 Codex 登录凭据已复制至隔离的 `Settings.codex_home/auth.json`，权限0600，
未改动用户 Codex 配置；后续登录/刷新凭据仍由正式运行时管理，本轮不实现 onboarding。
Engine RPC `external_events_status` 返回 Inbox pending/processing/done、已处理轮数、
工作器错误和来源 running/error。Inbox 在 `<data_dir>/external-events/inbox.sqlite`，权限0600。
当前监听所有新入站消息，模型决定是否提醒；未增加未读过滤、不修改已读。

本轮验证：新增13项持久交接/重试/引用校验/静默/上下文/工具证据/组装/恢复/语音格式/
实时消息优先测试通过；主项目和微信实验共558项通过、13项跳过、90个 subtest 通过；
新增链路显式 Pyright 零错误。
真实 Codex 结构化调用通过，BoxAgent 桌面及 Engine 已启动且 health=ready；
2026-11-17 以人工合成消息走真实模型的完整后端链路通过，用时6.377秒，
Inbox done=1、pending/processing=0、通知1条、Product user message=0；
该测试只验证通知请求和持久 Outbox，不把模拟回执记为用户实际看见。
结果保存在 `.runtime/private/artifacts/wechat-agent-inbox-smoke-20261117.json`。
随后以合成来源连接真实 Engine 和正式 Electron 宿主进行桌面验收：8.140秒后提醒出现在
可见气泡，系统通知返回 submitted 回执，Outbox delivered=1，assistant 历史保留一条提醒，
Product user message=0。submitted 只说明已向系统提交通知，不代表用户实际已读。
测试使用独立临时目录和测试资源服务，不读取微信；结果见
`.runtime/private/artifacts/wechat-event-ui-smoke-20261117.json`。
复测命令：

```bash
UV_CACHE_DIR=/tmp/uvcache uv run --python 3.12 \
  --with 'pillow>=11,<13' --with 'websockets>=15,<17' --with 'pyaudio>=0.2.14' \
  python -m experiments.wechat_replica.event_ui_smoke
```

脚本调用真实模型，仅发送固定的合成会议询问；会短暂打开独立 BoxAgent 测试气泡并发一条
合成通知，完成后关闭测试实例，保留现有实例和数据。需已有 Codex 登录及桌面构建。
随后用户明确允许持续监听，并将消息正文、会话/发件人及相关上下文交给已登录的
Codex/OpenAI 模型生成提醒。本机 `.env.local` 已开启微信模块，正式 Electron/Engine 持续运行。
真实新消息验收（2026-11-17）：

- 用户通过另一账号/朋友发来的测试消息触发原生数据库变化与监听事件，源 outbox 已确认交接。
- 通用 Inbox 持久接收，真实 Agent 返回 notify，通知 Outbox 获得系统 submitted 回执。
- 消息发出 → 系统通知提交9.181秒；监听捕获 → 系统通知提交7.781秒；Inbox 提交 → 回执7.779秒。
  微信消息时间为整数秒，9.181秒是近似端到端测量，不代表长期延迟分布。
- 消息的 source/account/subject/sender/event 引用与通知、助手历史逐项一致；助手历史仅一条，
  未伪造 Product 用户发言，也未将系统提交回执解释成真人已读。
- 验收快照：Inbox done=428、pending=0、processing=0；53个处理批次，13条提醒均有投递回执；
  source_pending=0，微信监听 running=true，来源和工作器错误均为空。
- 处理中正常重启 Engine 后，沿用原游标和 Inbox 恢复，无重复测试消息提醒。
- 没有自动回复联系人、没有朋友圈测试、没有新增密钥挖掘。当时语音凭据尚未配置，
  真实微信消息的验收使用桌面通知，不能据此声称微信语音播报已验证。

真实验收记录（仅状态、事件引用和时间戳，无正文/姓名）位于
`.runtime/private/artifacts/wechat-agent-integration-20261117.json`，权限0600。
当前监听持续开启；如需停止，设置 `BOXAGENT_WECHAT_ENABLED=0` 后重启 Engine。
原样全仓 pytest 的13项既有收集错误仍来自第三方实验与基础临时环境缺依赖。

2026-11-17 随后用户填好 `.env.local`：DashScope/Workspace 的 AOQ Token 鉴权通过，
DeepSeek 模型列表鉴权通过；未输出任何凭据。安装了项目固定版本且通过 SHA256 校验的
AOQ SDK 1.3.0，`auto` 现在可选中正式原生传输。任务 Provider 仍为 codex，没有自动切换。

实播发现并修复了一个 AOQ 通知回执问题：数据轨先收到 response.created，原生媒体轨
随后播放时，没有 response.audio.delta，通知编号就没有绑定到播放回调。
现在同时处理媒体先到和数据先到两种顺序，并在普通回复开始时清除旧关联，防止误确认。
新增两项回归测试；主项目及微信实验560项通过、13项跳过、90个 subtest 通过，
默认 Pyright 零错误（保留既有 pyaudio source warning）。

固定测试句“语音配置已经连接成功。”已完成 AOQ 原生播放、播放回执、音频完成与转写核验；
本次测试不开麦、不发送真实聊天历史或微信消息，不能等同于真实微信语音提醒验收。
结果位于 `.runtime/private/artifacts/voice-preflight-verified-20261117.json`，权限0600。
应用已重新加载配置与修复，麦克风维持关闭。该机器的 Jev-Mem 独立运行环境仍未安装，
长期记忆运行不在这次凭据与语音验证的通过范围内。

同日随后按用户要求，将本机 `BOXAGENT_TASK_PROVIDER` 从 `codex` 切为 `deepseek`。
执行器仍为 Codex App Server，任务、微信事件判断和上下文压缩的模型 API 改为 DeepSeek，
当前模型 `deepseek-flash`；AOQ 实时语音与百炼决策服务不受该参数影响。
切换前已通过 Codex App Server 对 DeepSeek 完成真实结构化请求验证（仅合成数据，2.742秒），
记录见 `.runtime/private/artifacts/deepseek-provider-preflight-20261117.json`。
已重启整个桌宠，实机进程参数确认为 `--task-provider deepseek`，Engine ready、微信监听正常。
本机配置、`.env.local.example` 和 `docs/setup.md` 均已补充“API 来源与执行器”的区别及
启动参数优先级，避免将 `codex` 误解成所有 Codex App Server 路径的通用名称。

App Server 配置依据：[OpenAI 官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)
与 [App Server 文档](https://learn.chatgpt.com/docs/app-server)，并用本机0.166.0-alpha.6
生成的 JSON Schema 和真实结构化轮次核验；不假设 dynamicTools=[] 会自行关闭所有内置工具。

DS 原有 `addresses / executor / probe / sender` 布局保留。下文八条事实仍是 ABI 约束。
发送沿用“导航 → 当前 cell/peer 核对 → 空草稿 → 写入 → 完整回读 → index8/action3
发送 → 查库等异步 server_id”的流程。实现调整：

- 基址、进程身份和活对象每次动态解析，不保留跨进程绝对地址。
- 验证构建、ARM64 slice SHA-256、磁盘/内存 UUID、PID 启动时间及 audit token。
- 对象查找验证会话向量和 QObject parent/back-pointer，拒绝不确定的多实例组合。
- 文本使用自己分配、正常引用计数的 QString；去掉原骨架的固定 scratch 地址。
- 核对目标、草稿、完整 UTF-16 回读和发送放在同一 Qt 主线程表达式内。
- LLDB 用结构化命名结果，失败不按位置错配；超时请求自身执行器退出，finally detach，
  不全局清理其他 debugserver。
- request_id 先持久化再调用原生操作；相同编号重跑返回旧结果，参数不同报冲突。
- 回执匹配只接受发送前游标之后、本账号发出、正文一致的唯一记录。
  server_id=0 时继续等，默认最多25秒。超时后保持未确认，重跑同编号不重发。

### 本机 CLI

在仓库根目录使用 Python 3.12；`zstandard` 用于解码压缩消息：

```bash
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat doctor
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat contacts '张三'
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat sessions -n 10
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat unread -n 5
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat history filehelper -n 20
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat search '会议' --in '群名'
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat members '群名'

# 预演会后台选择目标并检查空草稿，但不写入、不发送。
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat \
  send '你好' filehelper --dry-run

# 显式实际发送需要请求编号；一次新操作一个编号，结果不明时保留原编号。
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat \
  send '你好' filehelper --request-id example-send-001

# 群 @ 的身份先从本地群成员表核实；本地备注不会作为原生显示名。
uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat \
  send '你好' '群名' --mention '成员昵称或wxid' --request-id example-mention-001 --dry-run

uv run --python 3.12 --with zstandard python -m boxagent.entrypoints.wechat listen --duration 180
```

全局参数放在子命令前：`--db-root`、`--keys`、`--app`、`--data-dir`、`--self-wxid`。
默认读取现有 `.runtime/private/artifacts/wechat-key-20261113/derived-keys.json`；
该路径只是本机迁移配置，后续 onboarding 应提供正式密钥存储引用。
默认运行数据位于 `.runtime/pet/wechat/`，账号隔离，目录0700、文件0600。

`listen` 的本地目的地为 `local-events.jsonl`；其 EventSink 在落盘并 fsync 后返回确认。
源游标和待投递事件在同一事务提交；目的地确认前保留源 outbox，失败继续重试。
这不是恰好一次的外部副作用保证，其他消费者也应按 event_id 幂等。
首启建立基线，重启补收间隙消息，自己发出的消息不触发入站事件。
当前监听新入站消息，未加入“只通知仍未读消息”的产品规则，也不修改微信已读状态。
新增分片、数据库代际变化或游标回退会明确报错，等待检查，不静默清空状态。

CLI 退出码：0 表示查询/预演通过或监听正常结束；1 本地失败/明确拒绝；
2 收件人歧义需选择候选；3 表示提交未确认或结果不明。
非零 server_id 支持服务器接受，不能表述成对端已读。

### 应用与事件接口

`bootstrap.wechat.build_wechat_module(config, event_sink=...)` 组装实际依赖。
直接读取和发送不需要启动监听；调用 `await module.start()` 才建立监听基线，
`await module.close()` 等待当前读取完成再关闭状态库。
本地 CLI 使用 `build_local_wechat_module()`；Engine 启用模块时注入 `EventRuns` 作为
EventSink，共用事件协议无需依赖微信数据库结构。

`build_wechat_tools(module, allow_write=...)` 提供工具定义及调用网关。
默认只读；写入工具根据调用方已有授权开启，不自动读取第三方聊天内容来授权发送。
网关只依赖 `domain/wechat/contracts.py` 的应用能力端口。

### 当前范围与实机验证

正式模块已读取本机6272条联系人记录，成功查询会话、未读、文件传输助手历史和
一个群的381名成员（仅记录数量，没有将正文写入普通日志）。文件传输助手文本
发送只读预演通过，返回 `dry_run_passed` / `submitted=false`。
生产监听已完成多轮无错误的基线/空闲轮询；用户发送的新消息已通过正式模块产生1个
唯一事件，源 outbox 中 accepted=1、pending=0，本地 EventSink 已持久保存。
实验监听此前真实14条消息的验证见 `experiments/wechat_replica/LISTEN_PROBE.md`。

本轮没有向联系人新增消息。朋友圈适配器迁入时做过一次私密预演，没有发布；
用户明确降低优先级后不再追加朋友圈实机验证。

群 @ 只支持已验证的短 Native24 字符串字段；昵称与 wxid UTF-8 超过22字节时拒绝，
不截断。暂未解析群内专用别名，参数对象72字节仍保留到客户端退出，能力属实验性。
朋友圈也保留异步根生命周期及提交结果未独立确认的限制，不作为本期验收重点。
未物化会话初始化、媒体收发、撤回事件和 onboarding 尚未实现。

验证结果：主项目和微信实验 `545 passed、13 skipped、90 subtests passed`；
默认项目 Pyright 无错误（保留既有 pyaudio source warning），微信模块、CLI、工具网关和
测试的显式 Pyright 检查无错误、无警告。按 AGENTS.md 原样全仓 pytest 仍有13项既有
收集错误，来自第三方 PersonOS/REA 实验及临时环境缺失依赖；未更改测试/类型配置。

以下保留 DS 原始交接结构与八条硬性事实；实际模块入口和发送封装以本节为准。

## 1. 现状：脚本在哪

| 用途 | 路径 | 说明 |
|---|---|---|
| 原生发送（稳定版） | `experiments/wechat_replica/native_send.py` | uv/Python 3.12 驱动，lldb 只作执行器；固定 RVA + 运行时 base |
| 同一命令的薄封装 | `experiments/wechat_replica/native_send.sh` | 调用上面的 CLI |
| 旧键盘方案 | `experiments/wechat_replica/send_text.py` | 已不是主路径，保留作对照 |
| 生产模块（新建） | `boxagent/infrastructure/wechat/` | `addresses` / `executor` / `probe` / `sender` |
| 领域端口（新建） | `boxagent/domain/wechat/` | `WeChatTransport`、`MessageHistory`、`SendOutcome` |
| 组装入口（新建） | `boxagent/bootstrap/wechat.py` | `build_native_wechat_sender(pid)` |

交接文档：

- `docs/wechat-use-逆向实验.md`：实验 1–12（含踩坑与方法）；
- `docs/wechat-native-send-任务交接.md`：导航阶段交接；
- `docs/wechat-send-下一阶段交接.md`：发送阶段交接（含 1.5 节 peer 布局与物化限制）。

## 2. 给 Codex 的硬性事实（不要重新推导）

1. **地址是 RVA，不是绝对地址**。运行时 `base + rva`，base 每次启动都变；
   之前把 `base+0x12FBF4` 手算成 `0x1290FBF4` 直接 `EXC_BAD_ACCESS`。
2. 4.1.15 的 RVA 表（已在 `boxagent/infrastructure/wechat/addresses.py` 固化）：

```
vm_vptr 0x99A1170   editor_vptr 0x999A3D0   view_vptr 0x999A960
navigate 0xA57524   metacall 0x69A0654      send 0x12FBF4
qarray_dealloc 0x6D227A8                  shared_null 0xA05B978
发送：metacall(view, 0, index=8, {0,&action=3})
```

3. 对象首字段是 **vptr = vtable 表头 + 0x10**；扫表头值会 0 命中。
4. peer 参数是 **24 字节 libc++ string 对象地址**（`cell+0x158`），
   短字符串布局：内容在偏移 0，长度在偏移 23。
5. `select_existing_peer` 只遍历已物化的会话（`vm+0xF8` 向量），
   未物化的 peer 即使字符串正确也返回 0；需要 `page_ensure_initialized` 一类入口。
6. `server_id` 是异步回执：发送后立刻查库可能是 0，要等（脚本默认 25 秒轮询），
   且绝不能在回执前重发。
7. 读草稿要传**合法空 QString**（`shared_null` 或 ref=-1/size=0/offset=24），
   全零 QString 会让 Qt 属性读取崩溃。
8. 用户草稿非空时**拒绝覆盖**；清空必须显式 `--clear`。

## 3. 模块结构

```
boxagent/domain/wechat/
  models.py      DraftSnapshot / SendOutcome / DeliveryCheck
  contracts.py   WeChatTransport / MessageHistory（Protocol）
boxagent/infrastructure/darwin/
  mach_reader.py 只读任务内存（从 experiments 提升，experiments 侧留兼容 shim）
boxagent/infrastructure/wechat/
  addresses.py   BuildProfile + 已验证 RVA 表 + UnsupportedBuild
  executor.py    ExpressionExecutor 协议 + LLDBExecutor（attach→expr→detach）
  probe.py       ObjectProbe 协议 + ReplicaProbe（扫 vptr、定位 peer 对象）
  sender.py      NativeWeChatSender：navigate→校验→读写草稿→发送
boxagent/bootstrap/wechat.py  build_native_wechat_sender(pid)
tests/test_wechat_native.py   纯单元测试（假执行器/假探针，不需要活进程）
```

分层约束：`domain` 不导入 `infrastructure`；`sender` 只依赖协议
（`ObjectProbe`、`ExpressionExecutor`），便于替换与测试。

## 4. 发送时序（NativeWeChatSender.send）

1. `base = executor.base_address()`（每次进程启动一次）；
2. `probe.locate(base, peer, profile)` → `{vm, editor, view, cell, peer_object}`；
3. 一次 lldb 会话内：`navigate` → 校验当前 cell → 读草稿（必须为 0）；
4. 一次 lldb 会话内：写 scratch（QString）→ `metacall(editor,0,16)` → 读回比对；
5. 一次 lldb 会话：`metacall(view,0,8,{0,&3})` 触发发送；
6. 可选：`MessageHistory.find_delivery` 查库确认落库与 `server_id`。

## 5. 原始交接时未完成的项目

- `MessageHistory` 的生产实现：现已迁入 `database/reader.py`，见顶部实现记录；
- 未物化会话的 `page_ensure_initialized`；
- 长时间稳定性：重启后重建、并发发送串行化、失败重试策略；
- `sns` 等其他能力的模块化（目前仍是实验脚本）。
