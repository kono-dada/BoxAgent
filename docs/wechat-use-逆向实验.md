# 微信只读复刻实验（macOS / WeChat 4.1.15）

## 状态总览

| 能力 | 状态 | 证据 / 位置 |
|---|---|---|
| 取得数据库密钥 | ✅ 完成 | passphrase 抓取 + 19/19 库首页 HMAC（实验 2） |
| 一致快照 + WAL 重放 | ✅ 完成 | `integrity_check` 通过（实验 3） |
| 读会话 / 读消息 | ✅ 完成 | `read_messages.py --sessions/--chat`（实验 3） |
| 向"已打开会话"后台发文本 | ✅ 完成 | 键盘方案（实验 4）+ 原生方案（实验 12） |
| **后台切换到任意会话（已物化）** | ✅ 完成 | `select_existing_peer` 返回 1，界面切到西一业主（实验 11） |
| 未物化会话（不在 26 cell 内） | ⬜ 未完成 | 需 `page_ensure_initialized` 一类入口（见下一阶段交接 1.5） |
| 原生读草稿 / 写文本 / 触发发送 | ✅ 完成 | 发送索引 **8**、`action=3`；两条消息有 server_id（实验 12） |
| 图片 / 文件发送 | ⬜ 未开始 | vendor 有独立原生路径（实验 5） |
| 接入 BoxAgent | ⬜ 等待验收 | — |

> 关键结论：wechat-use 的"任意会话 + 零闪屏"不是 UI 自动化，而是**进程内**调用
> `select_existing_peer(vm, peer, origin)` + `QMetaObject::metacall` 写文本/触发发送
> （实验 5、10、11、12）。本机 4.1.15 上已全部定位并实测通过：
> 导航 `0xA57524`、读草稿 `(editor,1,9)`、写文本 `(editor,0,16)`、
> 发送 `(input,0,8)` 且 `action=3`。

## 背景与范围

本文件记录本机（`/Users/isaac/rbm_project/BoxAgent`）接续的实验。目标不是复刻
wechat-use 的全部功能，而是先独立拿到「读自己账号的本地消息」这一条链路，并在验收
后再接入 BoxAgent。

## 实验 1：为什么 4.1.15 扫不到 raw key

### 1. 背景

- 副本 `~/Applications/WeChat-4.1.15-BoxAgent.app`（bundle id
  `com.tencent.xinWeChatBoxAgent`，4.1.15 / 270115）已重签名并登录；同账号登录会
  使主微信下线。
- 此前已在副本进程上取得 `task_for_pid` 权限，完整枚举并读取了约 1.3 GiB 可读写
  内存，没有找到任何 `x'<96 hex>'` 候选；按 salt 邻域与有限指针图也未找到能通过
  首页 HMAC 的 32 字节密钥。
- 已确认三个库首页被加密，salt 位于首页前 16 字节。

### 2. 问题

WeChat 4.1.15 是否仍把可直接使用的数据库 raw key 缓存在进程内存里？

### 3. 假设

4.1+ 不再缓存 raw key，只保留 32 字节 passphrase；每个数据库的 AES-256 密钥由
`PBKDF2-HMAC-SHA512(passphrase, db_salt, 256000, 32)` 在登录时派生一次。若假设
成立，则任何静态内存模式扫描都不可能命中，必须抓派生调用本身。

### 4. 如何验证

1. 静态：在 `Resources/wechat.dylib` 中定位 `CCKeyDerivationPBKDF` 的调用者，
   恢复它使用的轮数与参数结构。
2. 动态：在副本进程上对 `CCKeyDerivationPBKDF` 下条件断点
   （`x2 == 32 && x6 == 256000`），在用户重新登录触发派生时读取 `x1` 指向的 32
   字节 password 与 `x3` 指向的 16 字节 salt，随后立即 detach。
3. 用抓到的 passphrase 按上述参数派生每个库的密钥，并用首页 HMAC 校验；只有校验
   通过才算成功。

### 5. 结果

- 静态：REA（Kuna provider）显示 `0x5736980` 是 PBKDF 包装函数，按 `a1` 选择
  SHA1/SHA256/SHA512 三种 PRF；其唯一静态调用者 `0x573677c` 是一个 vtable 初始化
  函数，把 `0x5736980` 等 17 个函数指针写入对象偏移 `0x10`–`0x90`，说明这是
  SQLCipher provider 的方法表，而不是直接调用点。Kuna 不声明
  `resolve_containing_procedure`，函数边界改用本地解析 `LC_FUNCTION_STARTS` 得到。
- 外部公开资料与上述静态结论一致：4.1+ 只留 passphrase，raw key 需重新派生
  （[wcdb-key-tool](https://github.com/TANGandXUE/wcdb-key-tool)）。该结论只作为
  假设来源，最终以本机首页 HMAC 校验为准。
- 本机新增 `experiments/wechat_replica/lldb_capture.py` 与
  `derive_keys.py`；断点命令已用空跑验证可挂载、可解析、可 detach。
- 动态抓取结果见下方「实验 2」。

## 实验 2：抓 passphrase 并派生真实密钥

### 1. 背景

实验 1 的假设若成立，passphrase 只在登录时派生一次，之后常驻内存不再重算。

### 2. 问题

能否在不修改微信行为、不暂停进程、不接触网络的情况下，取得本账号副本的
passphrase，并独立验证派生出的密钥正确？

### 3. 假设

在 `CCKeyDerivationPBKDF` 上以 `x2 == 32 && x6 == 256000` 为条件断点，可在重新
登录时命中一次；读出的 32 字节即为 passphrase，用其对各库 salt 派生后应能通过首页
HMAC。

### 4. 如何验证

```sh
# 1) 挂断点等待登录（后台运行，最长 15 分钟）
UV_PYTHON=3.12 UV_CACHE_DIR=/tmp/uvcache uv run python -m experiments.wechat_replica.lldb_capture --timeout=900

# 2) 期间在副本微信里退出登录并重新扫码登录

# 3) 派生 + 首页 HMAC 校验（密钥只写入 0600 私有文件，不打印）
UV_PYTHON=3.12 UV_CACHE_DIR=/tmp/uvcache uv run python -m experiments.wechat_replica.derive_keys
```

判定标准：`derive_keys` 报告 `verified:derived-le`（SQLCipher 4 的 16 字节 IV +
64 字节 HMAC 预留、页号小端）。任何 `hmac-rejected` 都说明参数或 passphrase 不对。

### 5. 结果

- 第一次尝试失败：条件断点命中后客户端停在登录界面。原因是 lldb 的
  `breakpoint command add 1 -o ...` 单行写法在非交互会话里没有按预期注册命令，
  命令列表里只剩下 `quit`，命中后 lldb 无法退出，进程一直处于 stopped。
- 修正：改用多行 `breakpoint command add 1 … DONE` 形式；lldb 的 stdin 改用
  FIFO 由脚本持有，命中后 5 秒内没有取到数据、或出现非断点 stop 超过 12 秒，都会
  主动 `process detach`，不会再冻住微信。整条链路先用本地
  `CCKeyDerivationPBKDF` 测试程序验证通过（含 `--waitfor` 启动即附加）。
- 第二次尝试成功：退出登录会让副本 App 重启，因此必须让 lldb 等待"下一次启动"
  （`process attach --name WeChat --waitfor`）。抓到 salt
  `63e7bc53d16d8a35071f58c178d6119e` 对应的 32 字节 passphrase，进程随即自动
  detach，副本继续正常登录。
- 派生结果：**19/19 个数据库首页 HMAC 校验通过**（`derived-le`），
  passphrase 与派生密钥都只写入 `0600` 私有文件，未打印到对话或普通日志。

## 实验 3：一致快照、WAL 重放与真实消息读取

### 1. 背景

拿到密钥不等于能读：库很大、客户端还在写 WAL，而且消息正文按 WCDB 约定做了
zstd 压缩（`WCDB_CT_message_content=4`）。

### 2. 问题

能否在不写入源库、不暂停微信的前提下，得到**一致**的明文副本并读出真实会话与消息？

### 3. 假设

对每个库：复制 `db` 与 `-wal`/`-shm` 私有副本（重试直到源文件大小与 mtime 稳定）→
按 SQLCipher4 页面布局解密（16 字节 salt、80 字节 reserve = 16 IV + 64 HMAC）→
校验 WAL 头的 checksum 并只重放"最后一个提交帧"之前的帧 → 用 SQLite 打开明文副本
做 `integrity_check`。若成功，应能列出会话并解出 zstd 文本。

### 4. 如何验证

```sh
UV_PYTHON=3.12 UV_CACHE_DIR=/tmp/uvcache uv run --with zstandard \
  python -m experiments.wechat_replica.read_messages --sessions=10
UV_PYTHON=3.12 UV_CACHE_DIR=/tmp/uvcache uv run --with zstandard \
  python -m experiments.wechat_replica.read_messages --chat=西一业主 --limit=8
```

判定标准：明文库 `integrity_check` 为 `ok`；会话时间戳与当前时间一致（说明 WAL
重放生效）；文本消息可读。

### 5. 结果

- `message_0.db`、`contact.db`、`session.db` 等全部解密成功并通过
  `integrity_check`；WAL 重放后会话时间戳是当前时间（19:0x），说明最新消息不是
  只有主库里的旧数据。
- `--sessions` 能列出真实会话（含未读数、摘要、时间）；`--chat` 能按用户名或显示名
  定位会话，解出文本消息、发送者、时间，并对图片/视频/系统消息给出占位或提取后的
  文本。
- 采样时读到的内容属于用户本人账号，本文件不复述具体聊天内容。

## 实验 4：后台发送（不激活窗口）

### 1. 背景

只读链路已通。用户的目标是"打游戏时让 BoxAgent 代回"，因此必须在不把微信窗口抬到
前台的前提下发送，并且不能发错会话。

### 2. 问题

能否在不写进程内存、不激活窗口的情况下把一段文本发到指定的已打开会话，并在发送
前后证明目标正确？

### 3. 假设

- `CGEventPostToPid` 把合成键盘事件直接投递给目标进程的事件队列，不改变前台应用；
- 微信把未发送的输入按会话存成草稿，因此"不带回车地打一个唯一标记，再去读
  `session.db` 的 `draft`"可以判定当前打开的是哪个会话——不需要截屏，也不需要
  辅助功能树。

### 4. 如何验证

1. 先在 TextEdit（非前台）上验证 `keystroke_probe` 能投递文本；
2. 在副本微信的文件传输助手上发一条带标记文本，再用解密后的 `message_0.db`
   确认这条自发消息真的落到 `Msg_<md5(filehelper)>`；
3. 用草稿探针识别当前会话，并做一次"目标不匹配就不发"的守卫测试；
4. 需要一次性给 DSH Desktop 授予「辅助功能」权限，否则投递返回
   `accessibility-not-trusted`。

### 5. 结果

- TextEdit 非前台注入成功；副本微信第一条测试文本成功落到
  `Msg_9e20f478…`（filehelper），数据库可见。
- **发生一次误发**：22 秒后的第二条测试文本落进了 `49515324671@chatroom`，因为期间
  窗口里的当前会话变了。这证明"按 PID 投递"不会选会话，事后校验只能发现、不能阻止。
  教训：发送前必须有目标校验，不能只靠事后核对。
- 草稿探针有效：不带回车输入标记后约 1 秒，`SessionTable.draft` 就能读到该标记，
  并据此判断当前会话。守卫测试中，当前会话是 `25566628376@chatroom`，请求发往
  filehelper，工具输出
  `guard: open conversation is 25566628376@chatroom, expected filehelper; nothing sent`，
  一条都没有发出。
- 实现：`keystroke_probe.c`（投递/清空输入）+ `send_text.py`（守卫、发送、事后校验）。
  探针会保存并恢复用户已有的草稿文本，避免把用户输入清掉。

### 已知限制

- 仍然要求目标会话已经打开；尚未实现"自动切到目标会话"（需要驱动搜索/会话列表）。
- 发送成功以"数据库出现原文"为准，不代表对方已读；群发/撤回/多媒体未实现。
- 需要辅助功能授权；未授权时直接失败，不会退化成别的行为。

## 实验 5：wechat-use 的后台发送到底怎么做的

### 1. 背景

用户要求"像 wechat-use 一样，按任意会话后台发送、不阻塞前台"。它的安装器
拒绝安装（`setup app inventory differs`，release 资产与清单不一致），因此直接从
公开 release 下载 `wechat-native-owner`（v1.19.23）用 REA 逆向。

### 2. 问题

它的 CLI 发送是 UI 模拟，还是进程内调用？

### 3. 假设

它既然随包带 `debugserver`、并在文档里写"keyboard event + 调试接口"，
应该是在进程内用调试器直接调用微信自身的发送函数。

### 4. 如何验证

用 REA/Kuna 打开 `wechat-native-owner`，检索 `debugserver|lldb|attach` 与
`rva` 相关字符串，读取其内嵌的发送脚本。

### 5. 结果（关键证据）

该二进制把整套发送脚本以字符串形式编译进去，可以直接读到：

- 它用 **LLDB 附加微信**，在主线程处于 stopped 时用
  `EvaluateExpression` 直接执行 C++ 表达式（`process.SetSelectedThread(main)`）。
- 表达式里调用的都是微信自身的内部函数，地址 = `base + profile[..._rva]`：
  `qstring_factory`（构造 QString）、`text_meta` / `input_meta`
  （`QMetaObject::metacall` 调用 Qt 槽）、`image_factory`、`path_string`、
  `list_ctor` / `list_append`、`insert`、`cache_file_size`。
- 发送动作是给输入控件做一次 metacall：`((void(*)(void*,int,int,void**))@META@)`
  以 `(input, 0, 5, args)`、`action = profile['send_action']` 触发。
  **注意这是 4.1.9 的索引**；本机 4.1.15 实测发送索引是 **8**、`action = 3`，
  索引 5 对应其它方法（见实验 12）。
- 全程带 `!wx_is_app_active()` 断言：**微信在前台时拒绝发送**——这正是"零闪屏、
  不抢前台"的实现方式。
- 每个微信 build 的 RVA 表 `profile` 由它的服务端签名下发
  （`wechat-public-native-send/v1`、`UnsupportedBuild`、`native send source ABI rejected`），
  这就是它闭源、且必须限定 4.1.9 的原因。

结论：它**不是**模拟点击，而是**用调试器在微信进程内调用 Qt 对象的槽函数**
完成"填入文本 + 触发发送"。这和我们副本上已有的 `task_for_pid` 权限是同一类能力，
差别只在于它把每个版本的关键 RVA 做成了签名配置。

### 6. 对我们的含义

- 读取链路不需要它：我们已经用数据库直接读。
- 发送链路要复刻，需要为 4.1.15 找到同一组 RVA：
  输入控件实例、`QMetaObject::metacall`、文本设置槽、发送动作编号，
  以及聊天编辑器对象布局。这是下一步的逆向目标，可以完全用 REA 做静态定位 +
  lldb 做动态验证。
- 因为它对 `send_action` 等值做了签名配置，直接照抄 4.1.9 的常量到 4.1.15 不可行，
  必须逐 build 校验（这也解释了它为什么要"按指纹校准"）。

证据文件：`.runtime/private/artifacts/wechat-use-vendor/wechat-native-owner`
（0600，仅本机）。

## 实验 6：在 4.1.15 上复制"进程内发送"（进行中）

### 1. 背景

实验 5 从 wechat-use 的 `wechat-native-owner` 里读出了它完整的文本发送实现：
在微信进程内用 lldb 执行 C++ 表达式，构造 QString，对编辑器做一次
`QMetaObject::metacall` 写入文本，再对输入控件做一次 metacall 触发发送动作，
全程用 `!wx_is_app_active()` 保证不抢前台。要复刻到 4.1.15，必须重新定位它
profile 里的那几个 RVA。

### 2. 问题

4.1.15 的输入控件、编辑器对象和 metacall 在哪里？能否不靠它的签名 RVA 表找到？

### 3. 假设

`mmui::ChatInputView` / `mmui::ChatInputField` 是输入控件与文本编辑器，
它们的 Qt 元对象结构（vtable → metaObject / qt_metacast / qt_metacall）可以在
dylib 里静态定位；再用对象首字段的 vtable 指针在活进程堆里扫出实例。

### 4. 如何验证

1. 字符串 `mmui::ChatInputView`（`0x9e4aa00`）的代码引用 → `qt_metacast`；
2. `qt_metacast` 的指针引用 → vtable 槽位 → vtable 起始；
3. 读 vtable 头验证布局；
4. 用 MachReader 扫活进程可写内存里等于 `base + vtable_rva` 的 8 字节值。

### 5. 结果

静态定位成功：

| 项 | 值 |
|---|---|
| ChatInputView vtable | RVA `0x999a950` |
| ├ metaObject() | `0x130148` |
| ├ qt_metacast | `0x130164`（比对 `"mmui::ChatInputView"`） |
| └ qt_metacall | `0x130310` |
| ChatInputField vtable | RVA `0x999a3c0` |
| └ qt_metacast | `0x12f8a4`（比对 `"mmui::ChatInputField"`） |

`qt_metacast` 里还能看到 `a0+0x168 / +0x170 / +0x178` 三个基类子对象偏移。

**但运行时扫描是否定的**：活进程可写内存约 1.3 GiB，收集到 21114 个指向
DATA 段的候选指针，**没有一个**等于这两个 vtable。也就是说 4.1.15 当前真正实例化
的输入控件不是这两个类（它们是基类或旧实现）。

补充证据：

- 两个类的 Itanium typeinfo 名字被混淆成 `_3d16f471` / `_dfb5955a`，
  说明**按类名/RTTI 反查在 4.1.15 上不可行**；
- 用唯一标记 `ZqMarkA1B2C3D4` 打进输入框（真实点击取得焦点后成功），
  在堆里找到 7 处 UTF-16 出现，但只有 2 处被指针引用，
  且**持有者附近没有 vtable**（字符串所在分配块不是 QObject），
  因此"从字符串反推控件对象"也断在对象边界上。

### 6. 关键旁证：vendor 自己也不扫堆

`wechat-native-owner` 里的 bootstrap 字段名暴露了它的做法：

```
inputview_bootstrap_pid      inputview_bootstrap_at
qt_hide_event_dispatch       qt_default_hide_event
kind0_primary_vtable         kind0_child180_primary_vtable
kind0_label_primary_vtable   ordinary_init_view
```

它在 **Qt 事件分发**上下断点，窗口隐藏/切换时事件带着 receiver 指针，
在那里"逮住"输入控件，再用 vtable 分类。这解释了它为什么必须按 build 下发
`event_dispatch_rva`、`core_event_call_rva`、`inputview_metacall_rva` 等 RVA。

### 7. 下一步

1. 在 4.1.15 里定位 Qt 事件分发路径（`QApplication::notify` 一带），
   以及它取 receiver 的位置；
2. 让用户切换一次会话，在断点上捞输入控件指针；
3. 对捞到的对象调用 `metacall(obj,1,9,&qstring)` 读文本，用标记校验；
4. 通过后按 vendor 的序列写文本并触发发送，`send_action` 在 0..16 内逐个试。

风险：第 2、3 步需要暂停进程并在进程内执行表达式，写错可能使微信崩溃
（用户已知悉并同意继续）。

## 实验 7：按键到底走哪条路（4.1.15 输入链路）

### 1. 背景

实验 6 的下一步需要在 Qt 事件分发上下断点，以便拿到输入控件指针。先用 AppKit 侧
的锚点找 Qt 的调用栈。

### 2. 问题

4.1.15 的键盘输入经过哪些函数？能否从 AppKit 侧稳定抓到"按键那一刻"的栈？

### 3. 假设

窗口 first responder 是 `QNSView`，那么按键至少会经过 `-[QNSView keyDown:]`；
在它上面下断点即可拿到 Qt 的 `notify` 栈。

### 4. 如何验证

1. `-[NSApplication sendEvent:]` 不带条件抓栈 90 秒（用户敲一次键），按栈签名分组；
2. `keyDown:` / `keyUp:` / `insertText:replacementRange:` 三组断点（`-S` 选择器）
   全部 armed 后再请用户敲键；
3. 每次用 `session.db` 的草稿判断按键是否真的进了微信。

### 5. 结果

- `-[NSApplication sendEvent:]` 90 秒内命中 405 次，但**按栈签名分组只有 1 组**，
  且都是约 4.5 次/秒的周期事件（`sendEvent:` → `wechat.dylib 0x6706a2c` →
  `WeChatAppEx Framework` → `-[NSApplication _handleEvent:]`）。命中多不等于抓到按键。
- `keyDown:`（86 个实现）、`keyUp:`（28 个）、`insertText:replacementRange:`（10 个）
  三组断点全部 armed 后再敲键：**命中 0**。
- 但同一时刻 `session.db` 的草稿里出现了 `aaaaaaaaaaaaa`——**按键确实进入了微信、
  确实写进了输入框**。
- 结论：4.1.15 的键盘输入**不经过 ObjC 响应链**（`QNSView` 虽是 first responder，
  但按键没有走 `keyDown:` / `insertText:`），说明 Qt 自己从事件队列取出 `NSEvent`
  后在 C++ 层处理。下一步应转向事件**拉取**层：
  `-[NSApplication nextEventMatchingMask:untilDate:inMode:dequeue:]`
  或 `-[NSWindow sendEvent:]`。

### 6. 事故与教训

- 曾用带**代码调用**的断点条件（`-c 'objc_msgSend(...) == 10'`）过滤按键事件，
  条件求值在暂停的目标线程里执行 `objc_msgSend`，导致微信死锁、界面卡死。
  处理：杀掉 lldb/debugserver 后 `kill -CONT <pid>` 恢复。**断点条件不要调用目标进程里的代码。**
- 抓取流程固定为：先启动 trace → 轮询日志出现 `Process ... resuming`（armed）
  → 再请用户操作；窗口 45 秒。attach 本身要 10 秒左右，过早操作必然漏抓。
- 测试期间在真实会话里留下过草稿（`qqqqqqq`、`aaaaaaaaaaaaa`），均已用
  `Cmd+A + Delete` 清除；清除前需要先让输入框获得焦点
  （`--realclick` 到窗口输入区坐标，窗口尺寸变化时要按比例重算）。

## 实验 8：从草稿字符串反查控件（失败）与点击焦点实测

### 结果

- 让输入框获得焦点后打入标记 `ZqM7Xk2Pilot`，堆里找到 3 处 UTF-16 出现；
- 对这 3 处做两级指针反查（字符串 → QString → 持有它的对象）：**0 个指针命中**，
  即字符串既不被数据指针引用，也不被 `-24` 处的 QArrayData 头引用；
  说明它属于 QTextDocument 内部的扁平文本缓冲，不是普通 QString 成员。
- `CGEventPostToPid` 投递鼠标点击（即使设置 window number）**无法让输入框获得焦点**；
  只有 HID 级 `CGEventPost` 真实点击（且窗口在前台）才行。
- 键盘导航（Tab/Down/Enter）从输入框转到会话列表**未成功**，输入框仍持有草稿。

### 当前卡点与下一步

纯后台发送仍缺"选中会话"这一步。下一步锚点已明确：

1. `-[NSWindow sendEvent:]` 或
   `-[NSApplication nextEventMatchingMask:untilDate:inMode:dequeue:]`
   （Qt 的事件拉取层）→ 找到 Qt 处理 `NSEvent` 的 C++ 函数；
2. 在该函数里捕获 receiver/QWidget 指针；
3. 用 vendor 的 metacall 序列验证文本读写。

已知限制：4.1.15 的 RTTI 类名被混淆，堆地址在秒级变化，实验会留下草稿
（需人工或重新聚焦后清除）。

## 实验 9：外部方案调研与 Accessibility 复核

### 调研到的同类项目

- **wechat-controller**（dragonking-T，MIT）：纯前台 GUI 自动化 —— cliclick 点击 +
  AppleScript 按键 + 激活微信；用视觉模型在搜索结果里挑正确的会话行。它明确说
  "微信的 UI 按钮没有暴露在 AX API 中"，所以走坐标点击。
- **wechat-fastbridge**（huj28-creator，MIT）：本地 MCP + 原生 Accessibility 桥。
  自述策略是"**先尝试后台控制**；当微信 4.x 把搜索结果行从 AX 行树里隐藏时，
  确认第一条结果、校验会话标题后再接受，然后**恢复之前的 App**"；并写明
  "自动选会话可能**短暂显示微信**，因为它的自定义结果行没有可靠的后台 press 动作"。

### 本机复核（重要修正）

之前"AX 树是空的"是在**进程被 lldb 暂停**时得到的错误结论。现在复核：

- `AXWindows` 返回 3 个窗口，窗口标题分别是 **`微信` / `西一业主` / `崔记烤串`**
  —— **窗口标题就是当前会话名**，可以在后台读取并用于校验目标；
- 但窗口内部只暴露 `AXButton` / `AXGroup`，**消息列表和搜索结果是空的**
  （与 fastbridge 的描述一致）。

### 结论

1. "后台读当前会话"可行：`AXWindows` 的标题就是会话名，可作为发送前后的目标校验。
2. "后台任意选会话"在 4.1.15 上**没有可用的 AX 路径**（行树被隐藏），
   同类项目也必须短暂前台化来选会话。
3. 我们的键盘注入问题也与此吻合：按键事件没有进入 `sendEvent:`（很可能被本地
   事件监视器在更早阶段截走），所以按 AppKit 响应链挂断点抓不到。

### 待验证

- 窗口标题是否能稳定反映"当前输入框所属会话"（多窗口时是否各自独立）；
- 用 `AXUIElementPerformAction` 对窗口/行做 press 是否有效（fastbridge 声称可用）。

### 实验 9 补充：1+2 的实测结果

- **窗口标题**：AX 侧有时能读到会话名（`西一业主`、`崔记烤串`），但同一进程的
  `AXWindows` 在两次相邻调用间会从 3 个变成 0 个，`AXFocusedWindow` 标题稳定为
  `微信`。作为"当前会话"的护栏**不可靠**，需要先解决 AX 时变性。
- **后台按会话行**：每个会话窗口的 AX 子树只有 3 个按钮（关闭/全屏/最小化），
  没有列表、没有行、没有输入框 → **后台切换会话在 4.1.15 上不可行**。
  这与 wechat-fastbridge 自述的"结果行不可后台 press、需短暂前台化再恢复"一致。
- **UI 框架**：NSApp 里同时存在 `QNSView`（Qt）与 `BridgedContentView`（Web 桥）
  窗口；`mmui::ChatInput*` 类在堆里没有实例，说明 4.1.15 的输入控件很可能已不在
  Qt 控件层，或被不同的具体类实现。wechat-use 的 Qt metacall 方案不能直接套用。

## 已知边界

- 只验证了 4.1.15 / 270115 这一个版本和本机这套参数（PBKDF2-SHA512、256000 轮、
  每库 16 字节 salt、80 字节 reserve）。
- 一致快照是"复制时源文件稳定"级别的近似；没有对源库加共享锁，极端写入窗口下仍需
  重试或改用锁快照。
- 消息正文只处理了明文与 zstd 两种编码；`local_type=49` 等结构化消息未展开。
- 发送只在文件传输助手和测试群做过带标记的验证，未写内存、未改客户端。

## 实验 3：REA 接入 DSH 的方式

### 背景

REA 6.9.0 的 MCP 服务会声明 255 个工具；仅 `direct`+`session` 两个 family 的
JSON Schema 就有约 819 KB，直接注册会显著挤占每次请求的上下文。REA 的
`REA_MCP_TOOL_FAMILIES` 只能按家族裁剪（`direct` 家族仍有 65 个工具）。

### 做法

新增 `experiments/rea_bridge/rea-mcp-gateway.mjs`：一个很小的 stdio MCP 网关，
对外只暴露两个工具，内部在首次调用时惰性启动真正的 REA MCP：

- `rea_catalog(tool?)`：列出 34 个允许的 REA 工具，或取某个工具的权威
  input schema；
- `rea_call(tool, arguments)`：转发一次调用。

DSH 侧通过 `@deepseek-ai/dsh-mcp-client` 注册（配置见
`~/Library/Application Support/dsh-desktop/harness/profiles/web/cordis.patch.yml`
的 `mcp-rea` 条目），暴露为 `mcp__rea__rea_call` / `mcp__rea__rea_catalog`。
REA 子进程使用满足版本要求的 Node（`~/.cache/codex-runtimes/.../node`，v24.22.1）。

### 结果

- 网关自身工具 schema 约 800 字节；REA 的 Kuna provider 可用，`open_binary`、
  `xrefs`、`procedure_pseudo_code` 在本机实际调用成功。
- 已知限制：REA/Kuna 不提供 `resolve_containing_procedure`，函数边界需本地解析
  `LC_FUNCTION_STARTS`；Kuna 的 `procedure_pseudo_code` 只接受其自身 inventory
  中的入口。

## 尚未完成

- 一致快照、真实会话与消息读取；
- 后台新消息监听（保持前台应用不变）；
- 发送：`mach_vm_write` 组织内部结构并由微信自身线程发送（用户已授权，但仍需先
  取得内部数据结构与发送入口的证据，风险显著高于读取）；
- 任意版本适配；
- BoxAgent 接入（等独立验收）。

## 实验 10：定位 4.1.15 的会话选择模型（进行中）

### 1. 背景

实验 5 证明 wechat-use 用 `select_existing_peer(vm.p, &peer, &origin)` 在进程内切换
会话。4.1.15 的二进制里同样存在 `mmui::ChatMasterViewModel`，因此尝试定位它的
vtable 与活实例。

### 2. 问题

4.1.15 的 `ChatMasterViewModel` 实例在哪里？`select_existing_peer` 的地址和签名是什么？

### 3. 假设

类名 → `qt_metacast` → vtable → 用 vtable 指针扫活进程堆，即可找到实例；
实例找到后再在其 vtable 槽位里定位选择方法。

### 4. 如何验证

1. `xrefs(0x9e4c860)` → 代码引用 `0x1350e8` → 所在函数 `0x1350cc`（qt_metacast）；
2. `xrefs(0x1350cc)` → vtable 槽位 `0x99a1178` → vtable 起始 `0x99a1160`；
3. 读 vtable 头验证布局：`[0]=0, [8]=typeinfo 0x99a11f0, [16]=metaObject 0x1350b0,
   [24]=qt_metacast 0x1350cc, [32]=qt_metacall 0x135164`；
4. 用 `base + 0x99a1160` 在可写内存里扫描（2 MiB 分块，MachReader 只读）。

### 5. 结果

- 结构定位成功（地址见上）；
- 但按 `ChatMasterViewModel` / `ChatInputView` / `ChatInputField` 三个 vtable 扫描
  活进程，**全部 0 命中**（包括打开主窗口「聊天」页之后）。

最可能的原因，按优先级：

1. **`wechat.dylib` 的加载基址变了**：本文件前面所有 RVA 都是相对基址的，若进程重启
   过，之前记录的 `base=0x128efc000` 就不成立，`base+RVA` 自然扫不到。**扫描前必须
   用 `lldb` 的 `image list -o -f` 重新取基址。**
2. 实际实例化的是**派生类**，vtable 不等于基类 vtable；
3. 实例化时机/内存区域不同（堆地址秒级变化，扫描与验证需要同一会话内完成）。

### 6. 下一步

1. 重取基址后重扫三个 vtable；
2. 若仍 0 命中：枚举活堆里所有对象首字段的 vtable 指针，反编译其 `qt_metacast`
   （`[vtable+0x18]`）反查真实类名，找出实际被实例化的 `mmui::*` 类；
3. 在 `ChatMasterViewModel` 的 vtable 槽位（`0x99a1160+0x28` 起）逐个反编译，
   找形如 `bool f(void* this, const void* peer, const void* origin)` 的函数；
4. 找到后先只调用选择函数，用 `session.db`/窗口标题确认切换成功，再谈发送。

---

### 实验 10 补充：2026-11-13，修正 vptr 地址点并定位导航入口

#### 1. 背景

用户要求从“主窗口打开后重扫”和“枚举活 vtable 的 qt_metacast 反查类名”两条线接续，限定本轮不激活微信、不写目标内存。随后用户要求试发，再要求先将结果回填交接文档供 DS 阅读。本次回填时，试发尚未执行。

#### 2. 问题

此前三个已定位类在堆中均零命中，曾推测基址变化、没有实例或实例属于派生类。需要先核对扫描值究竟是 Itanium 表头还是对象实际保存的地址点，再定位与 vendor `select_existing_peer` 行为对应的 4.1.15 函数及 ABI。

#### 3. 假设

表头的前两项是 offset-to-top 和 typeinfo；对象 vptr 应为 `base + 表头 RVA + 0x10`。活 vptr 对应的 `qt_metacast` 位于地址点 `+0x08`，可以离线解析其类名字符串引用，无须在微信进程内执行该方法。主模型的选择方法可能是普通成员函数，不能假定它位于 QObject 的虚函数槽中。

#### 4. 如何验证

- 用户手动打开主窗口后，用 `TASK_DYLD_INFO` + `MachReader` 被动读取本轮 image base，并重新扫三个原表头值；随后对同一进程补扫 `+0x10` 地址点。
- 连续三次复读候选 vptr，读取 live 表头和槽位；用客户端析构函数的静态 ARM64 指令交叉确认它向对象写入的地址点。
- 扫描 RW 区域指向 `__DATA_CONST` 的指针值，核对表头和代码地址范围；离线解析候选 `qt_metacast` 的 ADRP/ADD 字符串地址，先用三个已知类验证解析器。未调用目标的 `qt_metacast`。
- 从主模型的构造 / 析构和相邻普通方法入手，排除更改计数 / 删除会话等不匹配函数；定位现有 peer 查找、返回 0/1、调用内部选择逻辑的函数，再用活对象的数据字段和会话向量核对参数布局。

#### 5. 结果如何

- **已修正零命中原因**：base 本轮仍为 `0x128EFC000`；按旧表头扫描 991 个 RW 区域、读取约 1.5 GB，零错误且零命中。改扫地址点后，`ChatMasterViewModel` / `ChatInputView` / `ChatInputField` 各找到一处，故“这些类没有活实例”的旧推断不成立。
- **主模型已定位**：表头 RVA `0x99A1160`，对象 vptr RVA **`0x99A1170`**；PID `69581` 的本轮实例为 `0x7510A80298`。析构入口 `0xA4D360` 明确把表头 `+0x10` 写入首字段，并把 `0x99A11E0` 写入 `this+0x10`。
- **枚举已完成**：13,463 种候选指针值中识别出 **414 个 Qt vtable 候选、358 个不同类名**，无读取失败。`ChatSessionCellViewModel` 对象 vptr 为 `0x99A1238`，全堆有 51 处；这不等于当前主模型列表长度。
- **导航入口已定位**：对应 `select_existing_peer` 的普通成员函数 RVA 为 **`0xA57524`**，范围至 `0xA576D0`。ARM64 ABI 为 `x0=ChatMasterViewModel*`、`x1=const NativePeer24*`（24 字节 libc++ 字符串对象地址）、`x2=const OriginPair16*`（16 字节对象 / 控制块对地址）；`w0=0/1`。找到 peer 后调用 `0xA57D6C(this,&cell_pair,&origin_pair,0)`，未找到直接返回 0。不是 `const char*` 接口。
- **只读 ABI 交叉验证通过**：主模型 `+0xF8` 数据指针内的向量有 26 个条目，全部 cell 的 vptr 匹配 `0x99A1238`，全部 `cell+0x158` 可按上述字符串布局解析；包含 `filehelper`，且向量头读取前后一致。运行时入口前 32 字节与已分析二进制相同。
- **尚未执行原生切换或发送**：没有附加 LLDB 到微信、注入表达式、设置断点、调用激活 API 或写目标内存。仅生成了文件传输助手的调用前预检与准备表达式，不计作导航成功。
- **剩余工作**：确认 Qt 线程、对象生命周期和实际后台导航；再核对 editor / QString / metacall / send_action。尚未确认草稿为空，也没有填写或提交测试消息。

本轮证据位于 `.runtime/private/artifacts/wechat-qt-navigation-20261113/`，关键文件为 `live-vptr-validation-01.json`、`live-qt-class-enumeration-01.json`、`select-existing-peer-a57524-decompile-01.json`、`select-existing-peer-passive-validation-01.json`。REA 反编译 Evidence 为 `ev_21477dbfe19636d67c00c8cb11f2ba2ab01af4dfd3520aba7f15f933b5bd0928`。完整接续步骤见 [wechat-native-send-任务交接.md](wechat-native-send-任务交接.md) 顶部“最新增量”。

分析脚本显式 Pyright 为 0 errors / 0 warnings；工程 Pyright 为 0 errors / 1 个已有 pyaudio warning；按项目 README 的依赖运行主项目与独立实验，**474 passed、13 skipped、90 subtests passed**。根目录原样 pytest 会误收集下载的 REA 自身测试且缺少依赖，未改测试配置。

## 附录 A：地址与 RVA 速查

所有地址都是**相对 `wechat.dylib` 加载基址的 RVA**。基址每次启动都会变，用之前必须重取：

```bash
PID=$(pgrep -x WeChat | head -1)
printf 'process attach -p %s\nimage list -o -f\nprocess detach\nquit\n' "$PID" \
  | TERM=dumb lldb | grep wechat.dylib
# 例：0x0000000128efc000 .../Contents/Resources/wechat.dylib
```

| 对象 | vtable RVA | metaObject | qt_metacast | qt_metacall | 类名字符串 |
|---|---|---|---|---|---|
| `mmui::ChatInputView` | `0x999a950` | `0x130148` | `0x130164` | `0x130310` | `0x9e4aa00` |
| `mmui::ChatInputField` | `0x999a3c0` | — | `0x12f8a4` | — | `0x9e4a6a8` |
| `mmui::ChatMasterViewModel` | `0x99a1160` | `0x1350b0` | `0x1350cc` | `0x135164` | `0x9e4c860` |

vtable 布局（Itanium ABI + Qt moc）：

**这里的 `vtable` 指表头；活实例首字段保存的是 `vtable + 0x10`。** 因此从对象 vptr 出发，`metaObject` 在 `+0x00`，`qt_metacast` 在 `+0x08`，`qt_metacall` 在 `+0x10`。

```text
[vtable+0x00] offset-to-top（应为 0）
[vtable+0x08] typeinfo 指针
[vtable+0x10] metaObject()
[vtable+0x18] qt_metacast
[vtable+0x20] qt_metacall
[vtable+0x28..] 其余虚函数
```

## 附录 B：复现命令

从类名字符串定位 vtable 的三步：

1. `xrefs(类名字符串)` → 得到代码地址；
2. 用 `LC_FUNCTION_STARTS` 求该地址所在函数起点（即 `qt_metacast`）；
3. `xrefs(qt_metacast)` → 得到数据槽位，减去 `0x18` 就是 vtable 起点。

```bash
python3 /tmp/fstart.py 0x1350e8        # 0x1350e8 -> qt_metacast 0x1350cc
```

扫描活进程里的实例（只读、2 MiB 分块）：

```python
import struct, subprocess
from pathlib import Path
from experiments.wechat_replica.mach_reader import MachReader

pid = int(subprocess.run(['pgrep', '-x', 'WeChat'], capture_output=True, text=True).stdout.split()[0])
base = 0x128efc000  # ← 必须替换成刚取到的基址
exe = Path('/Users/isaac/Applications/WeChat-4.1.15-BoxAgent.app/Contents/MacOS/WeChat')
pattern = struct.pack('<Q', base + 0x99a1170)  # 对象 vptr 地址点：表头 + 0x10
with MachReader(pid, exe, max_bytes=2 * 1024 * 1024 * 1024, seconds=50) as reader:
    for region in reader.regions(max_regions=4096):
        if not (region.protection & 2):
            continue
        offset = 0
        while offset < region.size:
            size = min(2 * 1024 * 1024, region.size - offset)
            try:
                data = reader.read(region.address + offset, size)
            except Exception:
                break
            start = 0
            while True:
                index = data.find(pattern, start)
                if index < 0:
                    break
                print(hex(region.address + offset + index))
                start = index + 8
            offset += size
```

其它链路：

```bash
# 抓 passphrase（需在副本里重新登录一次）
uv run python -m experiments.wechat_replica.lldb_capture --wait-launch --timeout=900
uv run python -m experiments.wechat_replica.derive_keys

# 读会话 / 读消息
uv run --with zstandard python -m experiments.wechat_replica.read_messages --sessions=10

# 发送（目标会话必须已打开，守卫会在发送前校验）
uv run --with zstandard python -m experiments.wechat_replica.send_text --text='...' --expect=filehelper
```

## 附录 C：产物索引

| 路径 | 内容 | 权限 |
|---|---|---|
| `.runtime/private/artifacts/wechat-key-20261113/passphrase.json` | 32 字节 passphrase | 0600 |
| `.runtime/private/artifacts/wechat-key-20261113/derived-keys.json` | 19 个库的派生密钥 | 0600 |
| `.runtime/private/artifacts/wechat-key-20261113/plaintext/` | 解密后的明文库（含聊天内容） | 私有 |
| `.runtime/private/artifacts/wechat-handoff-20261113/wechat-415-arm64.dylib` | 分析用 arm64 瘦身二进制 | — |
| `.runtime/private/artifacts/wechat-use-vendor/wechat-native-owner` | vendor 原生发送二进制（v1.19.23） | 0600 |
| `.runtime/private/artifacts/wechat-use-vendor/owner.strings.txt` | 从上面提取的完整 Python 源码 | 0600 |
| `docs/wechat-native-send-任务交接.md` | 给另一个 agent 的交接说明 | — |

## 附录 D：事故与恢复

| 症状 | 原因 | 恢复 |
|---|---|---|
| 微信界面卡住、点不动 | 断点在主线程执行 `bt` 或在条件里调用目标代码（`objc_msgSend`）导致停顿/死锁 | `pkill -x lldb; pkill -f "LLDB.framework.*debugserver"; kill -CONT <pid>` |
| 微信停在登录/半渲染 | 断点命中后没有 detach | 同上；`ps -p <pid> -o stat` 应为 `S`/`R`，不是 `T` |
| 输入框里留下测试文本 | 注入实验 | 先让输入框获得焦点（真实点击输入区中心，窗口改过尺寸要按比例重算），再 `Cmd+A` + `Delete` |
| 数据库读报 `disk I/O error` | 快照复制与 WAL 写入竞争 | 重试 `refresh_db`；持续失败则重新快照 |
| AX 看不到窗口 | 进程正被 lldb 暂停 | 先 detach，再查询 `AXWindows` |

**三条硬规则**：

1. 断点条件**只读内存**，不要在条件里调用目标进程的函数；
2. 高频函数（`sendEvent:`、`nextEventMatchingMask:`）上不要挂 `bt`；
3. 任何 `base + RVA` 的计算前，先重新取基址。

## 实验 11：原生切换会话成功（关键突破）

### 1. 背景

实验 10 补充定位了主模型实例与 `select_existing_peer`（RVA `0xA57524`），但尚未调用。
本轮在活进程里执行了一次真实调用。

### 2. 问题

能否在不激活微信窗口的前提下，用一次进程内调用把当前会话切到指定 peer？

### 3. 假设

用主模型实例 + 模型里**已存在的 peer 字符串对象**（不自己构造 libc++ string），
在 Qt 主线程上调用 `select_existing_peer`，返回 1 且界面切到目标会话，并保持微信非前台。

### 4. 如何验证

1. 只读复核：`vm=0x7510A80298`（PID 69581，base `0x128EFC000`），首字段
   `= base + 0x99A1170`；`*(vm+0xF8)` 向量 26 项，peer 在 `cell+0x158`；
2. 取目标 peer 对象：`西一业主` = `25566628376@chatroom` → cell `0x750EC6C418`，
   peer 对象 `0x750EC6C570`（filehelper 为 `0x750C5AC018` / `0x750C5AC170`）；
3. 用 lldb Python API 在主线程执行
   `((bool(*)(void*,const void*,const void*))(base+0xA57524))(vm, peer, &{0,0})`，
   `SetStopOthers(False)` 让其它线程继续跑，避免死锁；
4. 调用后检查：返回值、前台应用、进程状态、窗口标题，最后由用户肉眼确认。

### 5. 结果

- `NAV-RESULT: 1`（peer 命中并进入选择分支）；
- 调用期间与调用后，**前台应用始终是 DSH Desktop**，微信没有被激活；
- 进程状态 `S`，无卡死；随后 lldb 正常 detach；
- **用户确认：微信界面已经切到「西一业主」群**。

这是 wechat-use "任意会话 + 零闪屏" 的核心能力，在本机 4.1.15 上首次复现成功。
本轮没有写进程内存、没有注入代码、没有设置断点、没有按键注入。

### 6. 复现要点

- peer 参数是 **libc++ string 对象地址**（`cell+0x158`），不是 `const char*`；
  直接用模型里已有的对象可以完全避开字符串构造；
- 必须在 **Qt 主线程**执行，且 `SetStopOthers(False)`；
- 实例地址绑定进程，重启后必须重新扫描（用 `base+0x99A1170` 作 vptr 地址点）；
- 返回值 1 只代表走了选择分支，界面切换需另行确认（本轮由用户肉眼确认）。

### 7. 下一步

1. 复用同一模型拿到"当前会话"的客观信号（不止靠肉眼）；
2. 定位文本输入路径：`qtextedit_metacall` / `inputview_metacall` /
   `qstring_factory` / `send_action`；
3. 先只读草稿（`metacall(editor,1,9,&qstring)`）并与数据库草稿交叉验证，
   再写入文本，最后触发发送；
4. 发送后用 `message_0.db` 的唯一标记文本确认落库。

## 实验 12：4.1.15 后台读草稿、输入中文、原生发送成功

2026-11-13，沿用实验11的副本与活实例，完成发送后半段。用户先明确允许 filehelper 单次测试，随后放宽本轮内存写入限制，最后明确要求向「西一业主」群发送一段助手自我介绍。

### 1. 先纠正交接假设

从活进程 QMetaObject 读取方法/属性名，并与本版静态分派汇编对照，确认：

- 不能把对象虚 `qt_metacall` 与类内局部索引混用；改用对应 `qt_static_metacall`。
- `QTextEdit` static metacall RVA `0x69A0654`：属性9=`plainText`、方法16=`setPlainText(QString)`。
- `ChatInputView` static metacall RVA `0x12FBF4`：方法8=`Send(SendActionType)`；旧文档的方法5在本版是 `AdaptiveContentChanged()`。没有盲调旧索引。
- `QString` 为一指针 QArrayData 布局；输出不能初始化成 null，因为 getter 会交换输出并释放原值。采用已核验的空数据哨兵 RVA `0xA05B978`；返回数据按 refcount 规则用 RVA `0x6D227A8` 释放。
- 从本版自己的调用点先读出 `w1=3` 再进入 Send RVA `0x839608`，据此选择 action=3，没有依次试发0..16。

### 2. 验证过程与结果

1. 当前会话原生 getter 读到长度0，与新快照的 SessionTable.draft 完全一致；调用后重新取当前 peer 和数据库，确认状态稳定。第一阶段只读取草稿，没有修改草稿。
2. 原生导航到 filehelper，读 `*(vm+0xF8)+0x40` 的当前 cell，再解码 cell+0x158 核实目标；原生与数据库均为空草稿。
3. 用户允许外部写入后，`mach_vm_allocate` 独立16KiB临时区，`mach_vm_write` 写手工 QString（ref=-1、UTF-16长度、offset24、正文与NUL）。setter 修改输入框；getter读回及新 session.db 草稿都与唯一标记逐字相同。
4. 发送前再次验证当前 cell/peer，getter逐码元比较待发文本；只执行一次 `input_static(input_view,0,8,{nullptr,&action3})`。
5. 重新快照 message_0.db，只查 filehelper 的 Msg 表：新增 **1条**完整正文匹配的消息，`local_id=3`，`server_id=8429112651288651490`。原生读回输入框长度0；最初数据库草稿有延迟，未因此重复发送。
6. 用户随后授权群消息：contact.db 精确匹配「西一业主」为唯一 peer，确认目标已在模型、群草稿为空，再后台导航、输入及发送一次。群 Msg 表新增 **1条**完整正文匹配记录，`local_id=75`、`server_id=4658905537814634224`，查库时草稿为空。

群消息正文（由用户要求介绍助手）：

> 大家好，我是 Isaac 的个人 AI 助手 BoxAgent，主要协助查资料、整理信息和处理日常事务。目前正在测试微信接入，这条介绍由 Isaac 授权发送。很高兴认识大家！

两次发送调用均返回完成，新增消息正文经解压后比对；非零服务器ID支持已提交发送，但没有另取对端阅读或送达回执。调用前后均检查微信非前台，未调用前台激活、按键注入或鼠标点击。

### 3. 执行细节与剩余限制

LLDB 在 Qt 主线程执行表达式，`SetStopOthers(False)`、15秒 expression 超时、unwind true、ignore breakpoints true；没有设置用户断点。每次调用 finally detach。它会在目标执行临时代码，不能描述为“全程不注入/纯只读”。一次误在 detach 后又执行 getter 时，主机 guard 报 Stale object，未进入原生调用；随后单独 attach 后正常通过。

独立静态 QString 区域两次共32KiB保留到副本退出，避免异步引用悬空；长期模块须完善所有权与回收。现有驱动绑定本次PID/对象地址，是实验材料，不是产品接口。已物化会话发送成功；未物化会话初始化、重启重定位、压力测试、并发与异常恢复仍待验证。

### 4. 可接续证据

- `.runtime/private/artifacts/wechat-send-20261113/qt-text-metadata-01.json`：活 metadata 与方法/属性名。
- 同目录 `current-draft-read-passed.json`、`filehelper-draft-read-passed.json`、`marker-read-passed.json`：分阶段读取验证。
- 同目录 `write-result-01.json`、`send-result-01.json`、`delivery-check-01.json`：filehelper 调用与新增消息记录。
- 同目录 `group-write-result-01.json`、`group-send-result-01.json`、`group-delivery-check-01.json`：授权群消息结果。
- 同目录 `native_step.py`、私有参数与单次发送 lock：一次性驱动、目标/草稿 guard、发送去重。
- `wechat-qt-navigation-20261113/send-handler-01.json`、`send-dispatch-01.json`、`send-action-01.json`、`send-button-caller-01.json`：REA/Kuna 静态依据，分析会话已关闭。

正确调用与下一阶段产品封装要求已重写到 `docs/wechat-send-下一阶段交接.md`。本轮未改生产代码或测试配置。

本轮验证：工程 Pyright 0 errors，保留既有 pyaudio source warning。按 AGENTS.md 原样运行根目录 pytest，因下载的 REA 自身测试导入路径和缺失 web/audio 依赖而在收集期失败；补齐临时依赖后，运行主项目与微信实验范围 `tests experiments/wechat_replica`，结果 **474 passed、13 skipped、90 subtests passed（22.69秒）**。未修改测试配置。`git diff --check` 仅指出既有 `.gitignore:54` 尾部空行，未改用户该文件；三份交接文档无行尾空白。

### 实验 12 补充：向「一场说走就走的旅行」群发送成功

- 目标：`34921055146@chatroom`（显示名「一场说走就走的旅行」），在模型向量第 25 项，
  cell `0x750c4f1c18`，peer 对象 `0x750c4f1d70`；
- 序列：`select_existing_peer` → 1；校验 `*(vm+0xF8)+0x40` 当前 cell 等于目标 →
  读草稿 0 → 写入 120 个 UTF-16 码元 → 读回 120 → 发送入口 `base + 0x12FBF4`
  `(input_view, 0, 8, {0, &action=3})` → 返回 1；
- 查库：`Msg_3ab5970eb42f6c4a7e82dfc7634fd2a6` 新增 1 条完全匹配，
  `local_id=3`、`server_id=8457988308579924227`（非零）、时间 22:07:00；
- 发送后草稿为空。

**踩坑记录（重要）**：`base + RVA` 必须用代码计算，不要硬编码。
`base + 0x12FBF4 = 0x12902BBF4`，我一度写成 `0x1290FBF4`（差 0x40000），
导致发送调用 `EXC_BAD_ACCESS`；用输入控件的 qt_metacall 槽位（`0x12902C310`）
代替该入口则会 `SIGABRT`。**发送必须用 `base+0x12FBF4` 这个入口。**

### 实验 12 补充二：脚本化发送器（稳定版）

把整条链路固化成 `experiments/wechat_replica/native_send.py`（uv / Python 3.12 驱动，
lldb 只做执行器）：

- 固定 RVA：`view_vptr 0x999A960`、`field_vptr 0x999A3D0`、`vm_vptr 0x99A1170`、
  `navigate 0xA57524`、`meta 0x69A0654`、`send 0x12FBF4`、
  `qarray_dealloc 0x6D227A8`、`shared_null 0xA05B978`；
- 运行时才取：`base`（lldb `image list`）、三个活实例、目标 peer 对象；
- 步骤：navigate → 校验 `*(vm+0xF8)+0x40` 当前 cell → 读草稿必须为 0 →
  写入 scratch 的 QString → 读回比对 → 发送（索引 8、action 3）→ 查库；
- 支持 `--dry-run`（不发送）与 `--clear`（原生清空草稿，不发送）；
- **不再硬编码绝对地址**：之前把 `base+0x12FBF4` 手算成 `0x1290FBF4` 导致
  `EXC_BAD_ACCESS`，改用 `base + RVA` 后稳定。

实测（22:20）：`49515324671@chatroom`（RBM story telling）发送成功，
`local_id=25`、`server_id=710648386162184003`，草稿发送后为空；
发送期间前台是 NeteaseMusic，微信没有被激活。

**坑**：文本文件末尾的换行会被算进正文，导致查库比对失败；脚本已 `rstrip("\n")`。

## 实验 13：原生群 @（真实 atuserlist）验证通过

2026-11-13，用户要求回复「一场说走就走的旅行」最近消息，先后台发送一条经上下文生成的回复；新增记录 `local_id=86`、`server_id=2872254297546321927`。随后用户明确要求在同群 @「立信」发送指定短句，本轮没有自动扩大回复或持续参与群聊。

从此前活 metadata 找到 `ChatInputView::InsertMention(kernel::AtUsersMemberInfo)`：本地方法索引 **12**，static metacall 仍为 RVA `0x12FBF4`。分派入口 `0x12FD4C` 最终调用 RVA **`0x83C910`**，内部传给编辑器实现 RVA **`0x8210FC`**。

静态分析 `0x8F1934` 证实，该路径读取参数对象的两个 libc++ string：偏移 **0x00 是目标 username**，**0x30 是显示名**，分别写入文本格式的用户属性 `0x10000A`、`0x100009`。这只确认本次路径访问的字段，不代表已经恢复完整结构。临时区至少提供72字节，三个24字节 string 槽均有效初始化；两项所需字段分别填唯一联系人精确 wxid 和「立信」。

执行流程：

1. 核对当前 cell/peer 是授权的旅行群；读取 contact.db，以备注「立信」精确匹配唯一联系人，与刚读取的群消息 sender 交叉核对。
2. 原生 getter 确认输入框为空；已有 session.db 草稿可能仍是上一条已发出的正文，不能据此覆盖真实未发内容。
3. 调用 `input_static(input_view,0,12,{nullptr,&member_info})` 插入真正的 mention 对象；用 `QTextEdit` static 方法 **27=insertPlainText(QString)** 追加指定短句。不能用 setPlainText，否则会破坏 mention 格式。
4. 原生 getter 的结果是 **U+FFFC + 普通后缀**，不是可视的 @ 昵称。临时区保存 getter 返回的 UTF-16 副本，经主机读取验证；session.db 草稿则是 `@立信` + U+2005 + 普通后缀，证明两层表示不同。
5. 发送前比较原生 U+FFFC 形式的完整草稿，并再次核对目标，只调用一次 action=3。查同一群的新记录：`local_id=90`、`server_id=570302896676118848`，正文精确匹配；解压 source 后 XML **atuserlist 精确包含目标联系人的 wxid**。这确认真实 @ 元数据已发送，不是只打了 `@立信` 字符；未独立验证对端设备展示或已读。

本次实际插入/读回/发送时微信均在后台，未调用激活 API。中间一次主机参数校验因长度写错而在调用前拒绝，修正后才调用；另一次检测到用户已将微信置前而拒绝，只读/发送未执行。随后将此明确授权操作的策略设为“允许用户已置前，但从不主动激活”，最终调用时记录仍为后台。临时区仍保留到进程退出，长期生命周期未产品化。

证据：`.runtime/private/artifacts/wechat-send-20261113/travel-mention-delivery-check-01.json`、`travel-mention-mention-result-01.json`、`travel-mention-send-result-01.json`；静态证据位于 `wechat-qt-navigation-20261113/mention-info-format-01.json`、`mention-insert-01.json`，REA会话已关闭。实验驱动扩充了本次用户授权目标和 mention 操作，未改生产模块。

实验13显示名审计补充：用户指出原生 @ 选择器显示 `Morty`。重新读取联系人与已发消息 source，确认 **nickname=Morty、remark=立信，两个名字对应同一 wxid**，消息 atuserlist 也正是该 wxid。当前手工参数 +0x30 使用了本地备注，导致显示 `@立信`，与用户原生操作显示不一致；目标身份未选错，但显示名选择尚未对齐原生行为。后续构造 AtUsersMemberInfo 应核验群内/原生显示名规则，不可把本地备注直接当作原生 @ 显示名。本轮只审计，没有补发。证据：私有 `mention-name-audit-01.json`。

### 实验 12 补充三：安慰消息与 server_id 回执延迟

- 目标：`53151132570@chatroom`（gkg看⚽️小组）；
- 发送成功：`local_id=21`，写入后立刻查库 `server_id=0`，约 12 秒后变为
  `1520419387499191619`；对方（杨家瑞）在 11 秒后回复 `local_id=22`；
- 结论：**`server_id` 是异步回执**。发送后立即读到 0 不能判定失败，
  验证应带重试窗口（脚本默认 25 秒轮询），且绝不能因此重发。

## 实验 14：纯文字私密朋友圈发布成功（2026-11-14）

用户先要求验证朋友圈发布，随后明确指定 **正文 `hi`、可见范围私密**。本轮实际启动一次发布订阅，最终用户明确反馈 **“看到了，是私密”**。这是当前 4.1.15 副本的独立原生实验；本地 wechat-use 能力说明只承诺朋友圈读取/刷新，本轮没有调用其发布实现。

### 验证结果与边界

- 原生朋友圈输入组件在隐藏状态创建成功；专用 QTextEdit 写入/读回 `hi` 一致。
- 用微信自己的隐私 setter 将模式从1设为 **2**；原生 UI 本地化值证明2对应 **“私密 / 仅自己可见”**，1对应公开。
- 提交前，实际内核数据模型中的正文和隐私字段再次核对为 `hi`、2。
- 发布操作通过原生 Observable/订阅启动一次，未调用激活 API、未使用鼠标/按键、未设置用户断点。输入与面板均隐藏，不调用普通 UI 的进度弹窗。
- 正常朋友圈缓存未找到这条私密帖子。进一步只读刷新后仍未找到；没有因此重发。最终成功与私密属性由用户实际查看确认，不能描述为已在本地数据库查到。
- 原生成功提示在模式2时使用 **“已保存到个人相册”**；普通时间线缓存不构成私密相册完整性证明。
- 本轮只验收一条纯文字私密帖子。公开/分组可见、图片/视频、稳定运行及产品封装均未验收。

### 本版地址与对象布局

均为本次 arm64 `wechat.dylib` 的 RVA，绑定 4.1.15 / build270115。重启后必须重新确认实例与基址。

| 项目 | RVA / 布局 | 本轮用途 |
|---|---|---|
| SNSWindowToolBar static metacall | `0x171D5C`，本地信号4 | OnPublishButtonLongPress，连接纯文字入口 |
| SNSContentView 的纯文字入口 | `0x1AC2714` | 区别普通图片入口 `0x1AC40B0` |
| 纯文字草稿获取后回调 | `0x1ACBE00` | 新内核模型构造 kind=1；正常 UI 还设置入口场景 |
| 文本发布 ViewModel factory | `0x19AF500` | 返回两指针 shared pair，非平凡返回使用隐藏 x8 |
| 文本 ViewModel vptr | `0x9B5D888` | 实测创建并绑定输入/隐私状态 |
| SnsPublishPanel 构造 / 绑定 VM | `0x19A2410` / `0x19A54F4` | 大小 `0x238`，本轮不 show |
| PublishInputView 构造 / vptr | `0x19BA7F8` / `0x9A0EBC8` | 大小 `0x198`，使用现有有效 style |
| 面板中的文字控件 | `panel+0x1B0` → `+0x190` → `+0x1E0` | PublishInputView → ReplyTextView → XValidatorTextEdit |
| QWidget static metacall | `0x6889B54`，ReadProperty35 | 确认窗口 visible=false |
| PrivacySelection 构造 | `0x19BC5B8` | 参数2映射本地化“私密”“仅自己可见” |
| 隐私状态复制 / 析构 | `0x19C6FFC` / `0x1F68A4` | 104字节状态；复制保留其它字段与容器 |
| 隐私 setter | `0x19DE150(vm,&state)` | 走正常 UI 的模型更新路径，状态首个 int=2 |
| 提交数据准备 | `0x19DECF8(vm)` | 不发网络请求；复制正文与隐私到内核模型 |
| 内核模型字段 | `*(vm+0x58)`；正文 `+0x58`；隐私 `+0x1F8` | 正文是 Native24 string，提交前核实 hi / 2 |
| 发布 Observable 构造 | `0x19DD910(vm)` | **惰性**构造，单独调用不表示已经提交 |
| 订阅启动 | `0xEB9DF0` → `0x24F228` | 实际启动发布，只调用一次 |

### 执行和生命周期要点

`QTextEdit` 的读写沿用实验12的 static metacall，但朋友圈使用新建的独立输入实例。不能复用聊天编辑器，也不能把私密枚举2猜成底层 bool。

原生返回的 shared pair / Observable / subscription 需要正确的 ARM64 返回 ABI 和有效生命周期。包含 `std::function` 小对象自引用的结构不能随意 memcpy 后让原内存过期。本轮用 **C++17 guaranteed copy elision + placement new**，把返回对象直接构造在持久的独立临时区中。LLDB 默认 ObjC++ 的 `__cplusplus` 是201103，首次 static_assert 在执行前拒绝；改用 `eLanguageTypeC_plus_plus_17` 后通过。

订阅使用两个诊断回调，复用了已核验的原生短回调：`0x9B6B9F8` 的 invoke 为 `0x1ACBAF8`，仅将捕获指针 `+0x1D0` 的字节置零，捕获目标是独立诊断区。两回调表示 on-next 与 on-completed，**不是成功与失败**；历史 JSON 的 `failure_callback_fired` 是最初命名不准确，源驱动现已改为 `completion_callback_fired`。回调未保存 on-next 中的 bool 值，因此最终结果仍以用户看到私密帖子的确认作为验收。

准备阶段另有 `empty` / `state` 的 LLDB 内部符号重名，均在编译期失败；改用独有变量名后通过。这些失败未执行发布。真正的订阅仅一次；私有 lock、持久阶段字和操作报告用于阻止未知状态下重发。

成功后清理新建隐藏面板与独立输入控件；隐藏面板清理还检查文字仍为 hi，避免关闭用户后来改动的内容。原生模型/Observable/subscription 根及其16KiB持久区本轮保留到副本退出，避免未经验证的释放破坏异步对象；长期运行必须补齐回收。没有删除已发表帖子。

### 可接续证据

私有目录沿用任务开始时名称 `.runtime/private/artifacts/wechat-sns-publish-20261113/`，最终验收日期为2026-11-14。关键文件：

- `sns-live-metadata-01.json`、`publish-static-metadata-01.json`：原生类与 Qt 方法。
- `create_input-result-01.json`、`input_roundtrip-result-01.json`：隐藏控件与读写验证。
- `create_panel-result-01.json`、`panel-runtime-parameters.json`：独立隐藏面板与 VM。
- `prepare_private_hi-result-01.json`、`prepare_payload-result-01.json`：hi 与私密2的二次确认。
- `authorized-submit.json`、`submit_private_hi-result-01.json`：具体授权、临时区、单次启动。
- `private-hi-delivery-check-02.json`、`private-hi-after-refresh-01.json`：本地普通缓存没有 hi，不能据此自动补发。
- `final-verification.json`：用户确认“看到了，是私密”。
- `sns_probe.py`：本轮一次性驱动，绑定当次对象、地址和仅此条授权；不是通用 CLI。

静态 Evidence 位于 `wechat-qt-navigation-20261113/sns-*.json`；REA 分析会话已正常关闭。本轮没有修改生产模块、依赖或测试配置。

实验14清理补充（已修复并验证）：最初清理把对象 vptr+8 错当作 deleting destructor；本版该槽实际是 **qt_metacast**，两次错误调用分别被 LLDB 以 EXC_BAD_ACCESS 拦下，unwind 后均成功 detach，微信 PID69581 保持运行。没有继续重试这个错误槽。正确处理改为已核验的 **QObject static metacall RVA0x6E5D5BC、本地方法3=deleteLater**，并对自建对象取得 Qt weak guard。主循环处理后，两个 guard 的 strong ref 均为0，确认隐藏发布面板与独立输入控件均已销毁，微信进程状态S。证据 `cleanup-verified.json`；错误报告保留为 `destroy_*-wrong-vtable-call-01.json`。不要再按 Itanium 常见表序猜 QObject 的析构槽。

本轮工程检查：Pyright 0 errors，保留既有 pyaudio source warning；主项目与微信实验范围为 **474 passed、13 skipped、90 subtests passed（22.31秒）**。按 AGENTS.md 原样根目录 pytest 仍受下载的 REA 测试导入路径及缺失 web/audio 依赖影响，12项收集错误；未改依赖或测试配置。真实输入、隐私、发布与清理由上述实机证据单独验收。
