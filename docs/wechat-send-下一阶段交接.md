# 微信 4.1.15 后台发送：三步验证已通过

2026-11-13，本机 **4.1.15 / build 270115 / arm64** 已完成：后台导航、读草稿、写中文、原生发送、指定会话查库。先向文件传输助手发送唯一标记，随后按用户明确要求向「西一业主」发送一次自我介绍。两次均新增一条正文完全匹配的消息，有非零 `server_id`；调用前后检查微信均未成为前台。没有对端阅读或送达回执的独立验证。

此前文档混用了虚函数与 static metacall，发送索引也不适用于本版，以下为修正后的接续依据。详细过程见 `docs/wechat-use-逆向实验.md` 实验 12。

## 已核验地址与索引

所有 RVA 相对本版 `wechat.dylib`；不要直接复用其它 build。

| 项目 | RVA / 本地索引 | 作用 |
|---|---|---|
| `ChatMasterViewModel` 对象 vptr | `0x99A1170` | 表头 `0x99A1160 + 0x10` |
| `ChatInputView` 对象 vptr | `0x999A960` | 表头 `0x999A950 + 0x10` |
| `ChatInputField` 对象 vptr | `0x999A3D0` | 表头 `0x999A3C0 + 0x10` |
| `select_existing_peer` | `0xA57524` | bool(vm, &NativePeer24, &OriginPair16) |
| `QTextEdit::qt_static_metacall` | **`0x69A0654`** | 使用下面的类内局部索引 |
| `plainText` | ReadProperty=1，index=**9** | 输出已初始化的 QString |
| `setPlainText(QString)` | InvokeMetaMethod=0，index=**16** | 写文本 |
| `ChatInputView::qt_static_metacall` | **`0x12FBF4`** | 本版的静态分派入口 |
| `Send(SendActionType)` | InvokeMetaMethod=0，index=**8** | 实测发送 action=**3** |
| `ChatInputView::Send` 实现 | `0x839608` | 静态分派最终进入此函数 |
| `QArrayData` 释放函数 | `0x6D227A8` | 实测 getter 结果的引用释放路径 |
| 空 QArrayData 哨兵 | `0xA05B978` | 本机头部 `(ref=0,size=0,alloc=0,offset=24)`，释放函数特判 |

**不能**把 `*(vptr+0x10)` 取出的虚 `qt_metacall` 直接搭配上述局部索引：虚调用还会处理继承类索引偏移。上述索引来自本版活进程 QMetaObject 数据和静态分派代码的交叉验证。

旧文档的输入视图 index=5 在本版是 `AdaptiveContentChanged()`，不是 Send。本轮未试调用错误索引。`action=3` 也先由本版自身调用点确认：`0x83D064`、`0x83D06C`、`0x849D40` 的路径最终以 `w1=3` 进入 `0x839608`，随后才实际发送。

## 导航与目标确认

1. 绑定副本路径、PID、启动时间、加载基址和对象 vptr；重启后所有堆地址失效。
2. `vm+0xF8` 指向数据对象；数据对象 `+0/+8` 是会话向量边界，每项 16 字节 `{cell,control}`。
3. 每个 `cell+0x158` 是 24 字节 libc++ string。短串字符从偏移 0 开始，长度在 byte23；长串最高位为1，头部指向堆数据。
4. 调用 `select_existing_peer(vm, cell+0x158, &{0,0})` 后，再被动读取 **数据对象+0x40 的当前 cell** 并比对其 peer。不能只凭函数返回1或窗口标题判定目标。
5. 群名先由 contact.db 精确匹配为唯一 peer，再与模型中的条目核对。

此函数只选择**已在模型中物化的会话**。此前本机 SessionTable 57 行而模型仅26项；不能把目前结果称为已支持任意未加载会话。未物化会话的初始化入口仍待定位。

## 第一步：只读草稿与数据库比对（已通过）

`QString` 在本版是一个 QArrayData 指针，头部 ref(int)+0、size(int)+4、alloc/flags+8、offset(int64)+16。观测到 metadata revision=8、Qt5 风格字符串与此布局；此前直接称其为“Qt6 QString”不准确。

输出对象必须初始化为有效空 QString，不能只给空指针：getter 内部交换输出值并释放旧值，旧值为 null 可能崩溃。

```cpp
void* q = (void*)(base + 0xA05B978); // 已验证的空数据哨兵
void* args[] = {&q};
text_static(editor, 1, 9, args);
// 读取 q 的 size/offset；检查边界，再比较 UTF-16 码元。
// 按 Qt 的引用计数规则释放返回数据，不能泄漏或重复释放。
```

本轮在当前会话读取空草稿，并在调用前后重新快照/解密 session.db，确认当前 peer 稳定、草稿完全一致。导航到 filehelper 后也复核空草稿；群发送前再次确认群草稿为空。

## 第二步：手工 QString 写入与读回（已通过）

用户随后明确允许 `mach_vm_write`。本轮用 `mach_vm_allocate` 在副本内申请独立 **16 KiB** 临时区，再用 `mach_vm_write` 写入头部和文本。没有按偏移直接覆盖微信原有对象字段；状态变更交给微信自身函数。

```cpp
// 远程临时区，初始为零：
// +0: ref=-1；+4: UTF-16码元数；+8: alloc/flags=0；+16: offset=24
// +24: UTF-16LE正文，末尾补两个零字节
void* q = scratch_header;
void* args[] = {nullptr, &q};
text_static(editor, 0, 16, args);
```

本轮两次分配共32 KiB，按静态 ref=-1 保留到副本进程退出，避免字符串被异步引用时悬空；尚未实现长期运行的临时区回收。后续产品实现应使用已核验的 Qt 所有权机制，而不是每条消息永久分配一页。

写入后用同一 getter 逐码元读回；并重解密 session.db，确认草稿等于待发文本。原草稿非空时停止，不清空用户内容。

## 第三步：单次发送与查库（已通过）

```cpp
int action = 3;
void* args[] = {nullptr, &action};
input_static(input_view, 0, 8, args);
```

发送表达式内部先重验当前 cell/peer，并原生读取草稿与待发文本逐码元比较，匹配才执行 Send。每个目标有独占发送尝试标记；发生异常或查不到结果时不自动重发。

只查询指定目标的 `Msg_<md5(peer)>`，比较发送前后的 `local_id` 和解压后的完整正文。本轮结果：

| 目标 | 新增完全匹配记录 | local_id | server_id | 结果 |
|---|---:|---:|---:|---|
| 文件传输助手 | 1 | 3 | 8429112651288651490 | 唯一中文标记；原生读回输入框已空 |
| 西一业主 | 1 | 75 | 4658905537814634224 | 用户授权的自我介绍；查库时草稿也已空 |

文件传输助手首次发送后，数据库草稿短暂仍是旧标记，但原生 getter 长度已为0；不能仅凭 session.db 的瞬时草稿判断是否发出。两条消息均有非零服务器 ID，但没有额外获取对端送达/阅读回执。

## 调用环境与证据

- Qt 主线程（queue=`com.apple.main-thread`）；LLDB `SetStopOthers(False)`、15秒 expression 超时、`SetUnwindOnError(True)`、`SetIgnoreBreakpoints(True)`。
- 未设置用户断点、未调用激活窗口 API、未注入按键；每次调用 finally detach 自己持有的进程，不全局杀其它 debugserver。
- LLDB 表达式求值会在目标进程执行临时代码；“原生调用”不意味着纯只读。用户已允许本轮原生调用、临时内存写入及指定消息发送。
- 私有动态脚本与结果：`.runtime/private/artifacts/wechat-send-20261113/`，其中 `native_step.py` 是本轮一次性实验驱动，参数绑定当次进程，**不能当通用发送 CLI 直接复用**。
- 元数据证据：`qt-text-metadata-01.json`、`validated-meta-indices-01.json`；发送查库证据：`delivery-check-01.json`、`group-delivery-check-01.json`。
- 静态证据：`.runtime/private/artifacts/wechat-qt-navigation-20261113/send-handler-01.json`、`send-dispatch-01.json`、`send-action-01.json`、`send-button-caller-01.json`。

## 下一阶段

增量（实验13）：真实群 @ 已验证。`ChatInputView` static 方法12=`InsertMention(kernel::AtUsersMemberInfo)`；通过有效临时成员参数插入 mention，再用 `QTextEdit` static 方法27=`insertPlainText` 追加正文。原生 getter 读到 U+FFFC 对象字符，session.db/消息正文则是 @昵称；不能用 setPlainText 重写 mention。已在用户授权的旅行群发出一条，source XML 的 `atuserlist` 精确包含目标 wxid，存在非零 server_id。字段布局、验证步骤及证据见实验13，尚未封装通用 mention API。

封装稳定的发送模块：按 build 校验配置、动态定位实例、管理 QString 生命周期、在 Qt 主线程串行执行、保护用户草稿、防止不确定状态下重发，并用目标会话消息记录核对结果。另需验证未物化会话初始化、重启后重定位、重复发送压力测试与异常恢复。本轮两条成功消息证明路径可行，尚不代表长期稳定性已完成。
