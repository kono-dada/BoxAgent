# 任务交接：在 WeChat 4.1.15 上实现"纯后台、指定任意会话发送"

> **2026-11-14 增量：私密文字朋友圈也已实发验证。** 用户指定正文 `hi`、可见范围私密；原生隐藏面板、输入读回、内核提交数据中的 hi / 隐私枚举2均验证通过，实际订阅只启动一次。普通 SNS 时间线缓存没有该记录，最终由用户明确确认“看到了，是私密”。不是仅凭调用返回推断成功。详见 `docs/wechat-use-逆向实验.md` 实验14。图片/视频、公开发布及生产模块均未验收。

> **最新结果（2026-11-13，Codex，实验12）**：后台导航 → 读草稿 → 写中文 → 原生发送 → 指定会话查库已全部通过。先向文件传输助手发送唯一标记，再按用户明确要求向「西一业主」发送一次自我介绍；各新增一条正文完全一致且有非零 server_id 的消息。调用前后微信均非前台，未设置用户断点或注入按键。
>
> 本版正确入口：`QTextEdit::qt_static_metacall` RVA **0x69A0654**，读取属性9、写方法16；`ChatInputView::qt_static_metacall` RVA **0x12FBF4**，发送方法 **8**、action **3**。虚 qt_metacall 不能直接使用这些类内索引；本版方法5不是发送。用户已明确允许 mach_vm_write，本轮只向新分配临时区写 QString，状态变更走微信原生函数。细节、所有权限制和证据见 [发送交接](wechat-send-下一阶段交接.md)。下文保留前几轮历史记录，以本段及实验12为准。
>
> **当前能力边界**：已物化会话的后台发送验证成功；未物化会话初始化、长期稳定性、重启重定位及产品模块封装尚未完成。非零服务器 ID 不等于独立核实对端阅读/送达。

> **DS 接续先读**：下方“最新增量”已经定位活实例和导航入口。旧版的“实例不存在 / 基址变化”推测已被本轮证据修正，请先看新增结果再动手。
>
> **2026-11-13 已执行（DS）**：用同一主模型实例（`0x7510A80298`）与模型内已存在的
> peer 对象（`西一业主` = `0x750EC6C570`，即 `cell+0x158`）实际调用了
> `select_existing_peer`（RVA `0xA57524`，`SetStopOthers(False)`，Qt 主线程），
> 返回 **1**，**微信界面成功切到「西一业主」群，全程前台始终是 DSH Desktop**。
> 细节见 `docs/wechat-use-逆向实验.md` 实验 11。
>
> **2026-11-13 后续（已执行并查库）**：读草稿 `(editor,1,9)`、写文本 `(editor,0,16)`、
> 触发发送 `(input,0,8)` 且 `action=3` 均已跑通；文件传输助手与「西一业主」各发送
> 1 条，正文完全匹配且都有非零 server_id，详见实验 12。

> **下一阶段交接**：文本输入与发送的详细步骤、表达式、验收标准与风险，
> 见 [wechat-send-下一阶段交接.md](wechat-send-下一阶段交接.md)。
>
> 地址表中的代码和 vtable 地址是相对 `wechat.dylib` 的 **RVA**；堆实例指针是绑定某一次进程的绝对地址，不能再加基址。每次重启都要重新确认进程身份、加载基址和实例。可用 `MachReader` + `TASK_DYLD_INFO` 被动读取；本轮没有附加 LLDB 到微信。

## 历史阶段记录：2026-11-13，只读定位完成时（后续执行见顶部与实验11、12）

### 已解决的卡点

旧表把 Itanium **vtable 表头**当作对象首字段的 **vptr 地址点**扫描，少加了 `0x10`。本轮重新读取 dyld 基址后，原表头值仍然零命中；改扫地址点立即找到实例。析构函数 `0xA4D360` 的机器码也明确先取 `0x99A1160`，再加 `0x10` 写入对象首字段。

| 项 | 本轮结果 | 验证范围 |
|---|---|---|
| `ChatMasterViewModel` 表头 RVA | `0x99A1160` | 表头是 offset-to-top、typeinfo 两项 |
| `ChatMasterViewModel` 对象 vptr RVA | **`0x99A1170`** | 对象扫描应使用这个值加基址 |
| `ChatMasterViewModel` 活实例 | **`0x7510A80298`** | PID `69581`，连续三次复读一致；重启后无效 |
| `select_existing_peer` 对应入口 RVA | **`0xA57524`** | 静态反编译与 ARM64 汇编交叉确认，尚未调用 |
| 入口运行时地址 | `0x129953524` | 仅对应本轮 base `0x128EFC000` |
| `ChatInputView` 对象 vptr RVA / 实例 | `0x999A960` / `0x750C0DA100` | Qt 类名引用与被动读取匹配 |
| `ChatInputField` 对象 vptr RVA / 实例 | `0x999A3D0` / `0x750EC7FB80` | Qt 类名引用与被动读取匹配 |
| `ChatSessionCellViewModel` 对象 vptr RVA | `0x99A1238` | 全堆观察到 51 处；不等于主模型当前列表条目数 |

枚举了 13,463 种指向 `__DATA_CONST` 的活指针值，按 Itanium 头部、函数地址范围及离线 `qt_metacast` 类名引用识别出 **414 个 vtable 候选、358 个不同类名**。没有调用活进程的 `qt_metacast`。注意：**表头 `+0x18` 等于对象 vptr `+0x08`**，才是本版本的 `qt_metacast`。

### 导航入口的 ABI 与语义

本版本与 vendor `select_existing_peer` 行为对应的函数范围是 `0xA57524..0xA576D0`。依据机器码恢复的调用边界如下；这些类型描述布局，不表示恢复了原始 C++ 类型名：

```cpp
struct NativePeer24 { unsigned long long words[3]; }; // 24 字节 libc++ string 表示
struct OriginPair16 { void* p; void* control; };       // 16 字节对象 / 控制块对
using SelectExistingPeer = bool (*)(
    void* chat_master_view_model,  // x0
    const NativePeer24* peer,      // x1，指向字符串对象，不是 const char*
    const OriginPair16* origin    // x2，指向对象对，不是按值传两个寄存器
);                                // w0: 未找到为 0，已找到并走选择逻辑为 1
```

静态行为：读取 `this + 0xF8` 的数据对象，再读取其 `[+0,+8]` 会话向量边界；每项 16 字节，对象 / 控制块各 8 字节。逐项比较 `cell + 0x158` 的 peer 字符串；未找到返回 0；找到后复制会话对象对和来源对象对，调用 `0xA57D6C(this, &cell_pair, &origin_pair, 0)`，释放临时引用并返回 1。该入口是**普通成员函数**，不在主虚表的剩余 QObject 虚函数槽里。

### 活进程只读交叉校验

- 主模型的 primary vptr 为 `base + 0x99A1170`，secondary vptr 为 `base + 0x99A11E0`。
- `*(vm + 0xF8)` 本轮为 `0x750E3E3C60`，向量内 **26 个条目**，读取前后边界一致；26 个 cell 的 vptr 均为 `base + 0x99A1238`。
- 26 个 `cell + 0x158` 的 24 字节字符串均可按上述短 / 长字符串布局读取，内容符合 ASCII identifier。未把真实 peer 或消息正文写入聊天输出。
- 向量中存在文件传输助手；本轮 cell 为 `0x750C5AC018`，已有 native peer 字符串对象为 `0x750C5AC170`。这些也是一次进程内的绝对指针，接续时必须重验。
- 入口运行时前 32 字节与已分析的 ARM64 文件一致，进程 UID / executable / 启动时间 / audit token 由 `MachReader` 绑定。

### 实际执行边界与用户最新要求

**没有执行原生切换、输入或发送；没有向微信写内存、注入表达式、设置断点或调用激活 API。** 本轮静态反编译由独立 REA / Kuna 进程完成，不是附加调试器到微信。

用户随后要求试发消息。助手说明先以文件传输助手作一次明确标记的验证，并完成了调用前预检；用户又要求**先把进度回填交接文档让 DS 看**，因此执行停在预检阶段。`navigation-call-parameters.json` 和 `navigation-expression.cpp.txt` 是准备材料，**不能作为已执行 / 已成功的证据**。

尚未核验：原生调用的实际 Qt 线程与生命周期、切换是否触发前台行为、`editor_offset` / `qtextedit_metacall_rva` / `qstring_factory` / `send_action`、输入草稿是否为空、真实发送与消息落库。旧 `send_text.py` 的按键方案不能作为本次 native 发送验证。

### DS 下一步

1. 复核当前副本身份和动态地址，使用新 vptr 地址点，不再扫描表头；实例重启后必须重新取得。
2. 延续用户的发送试验要求时，先只向文件传输助手验证一次导航：调用前后检查微信未成为前台，恢复运行后被动读取主模型当前 cell / peer，核对确为 `filehelper`。禁止靠前台激活或按键标记猜目标；调用结果未知时先查实际状态，不重复调用。
3. 导航确认后再定位并校验实际输入控件、草稿读取和发送动作；草稿非空则停止，不清除用户内容。一次发送后以指定会话中的唯一测试文本和消息记录确认，不能把方法返回或界面状态当作远端送达回执。

### 本轮证据（本机私有目录）

目录：`.runtime/private/artifacts/wechat-qt-navigation-20261113/`。

| 文件 | 内容 |
|---|---|
| `main-window-rescan-01.json` | 主窗口打开后按旧表头扫描，991 个 RW 区域，零命中、零读取失败 |
| `address-point-rescan-01.json` | 改扫 `+0x10` 后三个对象各一处命中 |
| `live-vptr-validation-01.json` | 三次 vptr 复读、所属可写分配区、实际槽位 |
| `live-qt-class-enumeration-01.json` | 414 个 vtable 候选 / 358 个类名及定位 metadata |
| `enumerate_live_qt.py` | 被动枚举、dyld 基址读取与离线 ADRP/ADD 类名解析脚本；输出使用独占创建，复跑前需选择新报告文件名 |
| `select-existing-peer-a57524-decompile-01.json` | REA / Kuna 反编译；Evidence `ev_21477dbfe19636d67c00c8cb11f2ba2ab01af4dfd3520aba7f15f933b5bd0928` |
| `select-existing-peer-passive-validation-01.json` | 主模型 / 向量 / peer 布局与入口代码的活进程只读核验 |
| `navigation-call-parameters.json` | 文件传输助手的预检及准备表达式，尚未执行 |

验证：分析脚本显式 Pyright 为 0 errors / 0 warnings；工程 Pyright 为 0 errors，保留已有 pyaudio source warning。原样根目录 pytest 受到下载的 REA 自身测试及缺失依赖影响；按主项目 README 准备依赖并运行 `tests experiments/wechat_replica` 后为 **474 passed、13 skipped、90 subtests passed**。没有改类型检查或测试配置。

> 本文件是给另一个 agent（Codex）的完整上下文。所有地址、命令、结论都来自本机实测。

## 0. 目标

BoxAgent 需要在**不激活微信窗口、不抢前台**的前提下，向**任意指定会话**发送文本消息。
读取链路已经完成，现在只差"选中会话 + 触发发送"。

参考实现：wechat-use（`~/rbm_project/BoxAgent/experiments/wechat-use` 是仓库源码，
`.runtime/private/artifacts/wechat-use-vendor/wechat-native-owner` 是 v1.19.23 的
原生二进制，里面的 Python 发送源码可用 `strings` 整段提取）。

## 1. 已确认的关键事实

### 1.1 wechat-use 的真实机制（从二进制内嵌源码读出）

**发送文本**（`qt_text.py` 风格，表达式模板）：

```c
// editor 来自导航快照；input 是输入控件
wx_text_meta(editor, 1, 9, &qstring);        // 读当前草稿，必须为空
wx_text_meta(editor, 0, 16, {nullptr,&s});   // 写入文本
input_meta(input, 0, 8, {nullptr,&action});  // 触发发送，action = 3（4.1.15 实测）
```

> **版本差异（重要）**：上面这段来自 wechat-use 的 4.1.9 实现，其中触发发送用的是
> `(input, 0, 5, args)`。在本机 **4.1.15** 上实测，发送方法索引是 **8**，
> `action = 3`；索引 **5 对应的是另一个方法**，不要照抄 4.1.9 的值。
> 已验证记录见 `docs/wechat-use-逆向实验.md` 实验 12。

- `wx_text_meta` / `input_meta` 是 `QMetaObject::metacall` 的地址；
- 全程 `!wx_is_app_active()` 断言：微信在前台就拒绝发送；
- 需要 `qstring_factory`（构造 QString）、`qarray_deallocate`（析构）。

**选中任意会话**（`qt_navigation.py` 风格）：**完全不碰 UI**，

```c
bool found = select_existing_peer(vm.p, &peer, &origin_pair);
// vm.p = mmui::ChatMasterViewModel 实例指针
// peer = 目标 wxid（ASCII，校验过的字符集）
```

它靠一张按 build 签名的 RVA 表（`rva-cache.json`，服务端下发）来定位：
`chat_master_view_model_primary_vtable`、`select_existing_peer`、`view_model_acquire`、
`weak_factory/release`、`pair_release`、`native24_construct`、`inputview_metacall_rva`、
`qtextedit_metacall_rva`、`editor_offset`、`editor_vtable_rva`、`send_action` 等。

**结论：任意会话 + 零闪屏是可能的，路径是进程内调用模型方法，不是 UI 自动化。**

### 1.2 本机环境

- 副本：`~/Applications/WeChat-4.1.15-BoxAgent.app`，bundle `com.tencent.xinWeChatBoxAgent`，
  版本 4.1.15 / 270115，ad-hoc 重签且有 `get-task-allow`（`task_for_pid` 可用）。
- 当前进程 PID 约 69581，加载基址 `wechat.dylib` base = `0x128efc000`
  （每次重启会变，需重新取：`lldb` 里 `image list -o -f`）。
- 已拿到数据库密钥、能读全部会话/消息（见 `docs/wechat-use-逆向实验.md` 实验 2-3）。

### 1.3 已定位的 4.1.15 结构

| 项 | RVA | 说明 |
|---|---|---|
| `mmui::ChatInputView` vtable 表头 / 对象 vptr | `0x999a950` / `0x999a960` | metaObject `0x130148` / qt_metacast `0x130164` / qt_metacall `0x130310` |
| `mmui::ChatInputField` vtable 表头 / 对象 vptr | `0x999a3c0` / `0x999a3d0` | qt_metacast `0x12f8a4` |
| `mmui::ChatMasterViewModel` vtable 表头 / 对象 vptr | `0x99a1160` / `0x99a1170` | metaObject `0x1350b0` / qt_metacast `0x1350cc` / qt_metacall `0x135164` |
| `mmui::ChatSessionCellViewModel` 对象 vptr | `0x99a1238` | qt_metacast `0x135368`；活进程中已观察到实例 |
| `select_existing_peer` 对应普通成员函数 | `0xa57524` | `x0=vm, x1=&NativePeer24, x2=&OriginPair16, w0=0/1`；尚未调用 |
| 字符串 `mmui::ChatMasterViewModel` | `0x9e4c860` | 类名存在于二进制中 |

**历史结果已修正**：原来扫描的是表头，故三个类都零命中。本轮通过 `TASK_DYLD_INFO` 重新确认 base 仍为 `0x128efc000`，改扫对象 vptr 地址点后均命中。不能再据旧扫描推断类未实例化或必须改找派生类。

### 1.4 已排除的死路（别重复）

1. **按 PID 投递鼠标点击**：`CGEventPostToPid` + window number 也无法让输入框获得焦点；
   只有 HID 级 `CGEventPost`（要求窗口在前台）有效。
2. **按 PID 投递键盘**：只在输入框已有焦点时有效；Enter 需要窗口保持 key 状态。
3. **AppKit 响应链断点**：`keyDown:`（86 处）、`keyUp:`（28 处）、
   `insertText:replacementRange:`（10 处）全部 armed 后敲键 **0 命中**，
   但草稿确实写入了数据库 → 按键被更早的阶段（本地事件监视器/输入法）截走。
4. **高频断点会卡死微信**：在 `-[NSWindow sendEvent:]` 上每次跑 `bt` 会让主线程卡住；
   带**代码调用**的断点条件（`objc_msgSend`）会直接死锁。恢复方法：
   `pkill -x lldb; pkill -f debugserver; kill -CONT <pid>`。
5. **AX 后台切会话**：每个会话窗口的 AX 子树只有 3 个按钮（关闭/全屏/最小化），
   没有列表/行/输入框；`AXWindows` 在相邻两次调用间会从 3 个变 0 个（不稳定）。
6. **从草稿字符串反推控件**：标记串在堆里能找到（UTF-16），但既不被数据指针引用，
   也不被 `-24` 的 QArrayData 头引用（属于 QTextDocument 内部缓冲），两级反查 0 命中。
7. **RTTI 类名被混淆**（`_3d16f471` 之类），不能按 typeinfo 名字识别类。

## 2. 当前卡点（下一步要解决）

活实例、入口 RVA 与参数布局的定位已完成，见顶部增量。当前卡点是实际后台导航及后续输入 / 发送链路的验证；本轮尚未调用导航入口，未发送消息。

## 3. 建议的下一步（按优先级）

按顶部“DS 下一步”接续。`0x9e4caa8` 是类名字符串附近地址，不能当作 `ChatSessionCellViewModel` 的 vptr；其实际地址点是 `0x99a1238`。主模型的 `select_existing_peer` 不是虚函数，继续逐个反编译 QObject 虚槽不会定位到它。

## 4. 工具与环境

- REA MCP 已接入 DSH：`mcp__rea__rea_call` / `mcp__rea__rea_catalog`（34 个工具）；
  也可直接用 `/tmp/fstart.py <addr>` 解析 `LC_FUNCTION_STARTS` 得函数边界。
- 二进制：`.runtime/private/artifacts/wechat-handoff-20261113/wechat-415-arm64.dylib`
  （arm64，SHA256 `6d3feb56043cb63d2ff3f77aa32115abbfe7a2f28b3ab1ab59886ee625703e4b`）。
- 活进程读：`experiments/wechat_replica/mach_reader.py`（只读、校验 PID/签名/启动时间）。
- 数据库/会话：`experiments/wechat_replica/read_messages.py`；密钥在
  `.runtime/private/artifacts/wechat-key-20261113/`（0600，勿外传）。

## 5. 约束

- 不允许"短暂前台/激活微信"的折中方案（用户明确否决）。
- 不要写进程内存、不要注入代码（除非用户另行授权）。
- 实验会留下草稿，需 `Cmd+A + Delete` 清除（先让输入框获得焦点）。
- 详细实验记录见 `docs/wechat-use-逆向实验.md` 实验 1-9。

## 6. 回填约定

- 每定位到一个新地址，请同时更新：
  - 本文件 1.3 的地址表；
  - `docs/wechat-use-逆向实验.md` 的实验 10（结果 + 验证方法）；
- 记录时务必写明"相对基址的 RVA"，不要写死绝对地址；
- 新增的死路请追加到第 1.4 节，避免其他人重复踩坑。
