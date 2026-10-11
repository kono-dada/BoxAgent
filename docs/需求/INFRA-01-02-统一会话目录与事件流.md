# INFRA-01/02 统一会话目录与事件流（设计稿）

> 状态：✅ 2026-10-12 已实施，验收见 [实施记录](2026-10-12-陪伴上下文实施验收.md)
> 依赖：REQ-01（账本）、INFRA-03（日志）已在本地提交
> 参照：本机 DSH 的 34 个会话（`~/Library/Application Support/dsh-desktop/harness/`）

---

## 0. 结论先行

1. **目标**：一个会话一个目录，一条事件流 `session.v1.jsonl` 按时间记下这个会话里发生的一切，并能回看每次模型请求的有效上下文、来源与增删变化，服务后续上下文展示。全局只留会话索引、长期记忆、通知和诊断日志。
2. **三条关键设计**
   1. **事件命名对齐 DSH**：`turn/start`、`user/message`、`assistant/message`、`turn/end`、`compaction/summary`。REQ-01 账本里“模型看到了什么”的条目统一改用 `context/*` 前缀，避免与“用户说了什么”混淆。
   2. **任务明细不进主流**：任务的心跳、协议帧等高频事件（单个任务约 150 条）放在 `tasks/<task_id>/events.jsonl`；主流只写 `task/start`、`task/end` 两条，并带上引用。这一点与 DSH 不同，理由见 §3.3。
   3. **迁移只复制、不删除**：旧目录原样保留，新目录写好后用一个标记文件切换；回滚先导出全部正式历史到独立 legacy 投影，再切换标记；原始目录与统一流均保留。
3. **分 4 个提交实施**，每一步都能单独测试和回滚（§6）。
4. 用户已授权自主推进；实现选择与旧设计差异见实施记录。

---

## 1. 术语

| 术语 | 含义 |
|---|---|
| 事件流 | 会话目录下 append-only 的 `session.v1.jsonl`。首行是会话头，之后每行一个事件：`{type, seq, time, data}` |
| 业务事实 | 用户说了什么、助手答了什么、回合起止、任务起止。丢了无法重建 |
| 上下文条目 | 模型实际收到的内容（REQ-01 账本），可以从 wire trace 重建 |
| 可变状态 | 会被原地改写的小文件，例如标题、Runtime 绑定。不进事件流 |
| 投影 | 从事件流计算出来的视图，例如消息列表、上下文构成。可随时重算 |

---

## 2. 现状

### 2.1 一个会话的数据散落 5 处

| 位置 | 内容 | 体积（本机） |
|---|---|---:|
| `conversations/sessions/<id>/events.jsonl` | 业务事实，4 种事件：`interaction.started`、`message.final`、`interaction.finalized`、`context.checkpoint.created` | 5 KB / 会话 |
| `conversations/sessions/<id>/context.v1.jsonl` | REQ-01 账本 | 28 KB / 会话 |
| `conversations/sessions/<id>/context.jsonl` | Checkpoint 与 Segment；12 个会话中多数是空文件 | — |
| `conversations/sessions/<id>/metadata.json`、`runtime-bindings.json` | 标题、归档、Runtime 绑定 | < 1 KB |
| `runs/<日期>/<时间-task_id>/` + `runs/by-session/` 软链 + `runs/index.jsonl` | 任务明细与截图 | 23 MB |
| `tasks/<task_id>/`、`logs/tasks/` | 旧版任务目录，仍有读取兼容 | 16 MB |
| `logs/runtime/qwen/events.jsonl` | 所有会话混在一起的 Qwen wire trace | 1.2 MB |

数据根目录下还有 `context-checkpoint.jsonl`、`boxagent.sqlite3`、`smoke/`、`ui-check/` 等散落文件。

### 2.2 已经做对的部分（保留）

1. 会话写入有 `fsync`，尾部撕裂可修复，中段损坏直接报错（`jsonl_session_repository.py`）。
2. 账本已用 DSH 风格：`{type, seq, time, data}`，首行是表头。
3. Engine 是唯一写入进程。

### 2.3 DSH 的做法（实测）

1. `session-<uuid>/` 下只有 `session.v4.jsonl.zstd`（或旧名 `session.jsonl.zstd`），首行 `{"type":"session","version":4,…}`。
2. 只有 4/11 个会话有 `session.lock`，说明锁是写入期间才创建的。
3. 磁盘上出现了 **42 种**事件，包括 `request/context`、`request/header`、`llm/retry`、`approval/*`、`compaction/*`：请求时的上下文也在同一条流里。
4. 索引另存：`storages/workspace.json`、`session_projcache.json`；附件按哈希存储：`attachments/v1/objects/`。

---

## 3. 设计

### 3.1 目录布局

```text
<BOXAGENT_DATA_DIR>/
├── sessions/
│   ├── index.json                    # 会话顺序、当前活跃会话（原 conversations/index.json）
│   └── <ses_id>/
│       ├── session.v1.jsonl          # 唯一事件流
│       ├── session.lock              # 写入期间持有的 fcntl 锁
│       ├── state.json                # 可变状态：标题、归档、Runtime 绑定（合并原 metadata.json 与 runtime-bindings.json）
│       └── tasks/<task_id>/          # 任务明细：task.json、request.json、result.json、events.jsonl、step-N.*
├── memory/                           # 不变
├── notifications/                    # 不变
├── skills/                           # 不变
├── attachments/                      # INFRA-04 再启用
└── logs/                             # 只放诊断数据（INFRA-03）
```

1. **会话目录自包含**：删除一个会话就是删除一个目录，隐私边界清楚。
2. **`runs/` 取消**：跨会话的“最近任务”列表改由 `sessions/index.json` 中的统计字段提供（INFRA-04 再扩展为缓存）。
3. **全局 wire trace 保留**，但只作为诊断数据；重建旧会话上下文时仍可读取。

### 3.2 事件流格式

```json
{"type":"session","version":1,"session_id":"ses_…","created_at":1791605233.77}
{"type":"turn/start","seq":1,"time":…,"data":{"interaction_id":"int_…","source":"text","runtime":"qwen_realtime"}}
{"type":"user/message","seq":2,"time":…,"data":{"interaction_id":"int_…","content":"…"}}
{"type":"turn/prepare","seq":3,"time":…,"data":{"interaction_id":"int_…","memory":{"status":"ok","ms":1412,"chars":450}}}
{"type":"context/item","seq":4,"time":…,"data":{"category":"memory","role":"system","content":"…"}}
{"type":"assistant/message","seq":5,"time":…,"data":{"interaction_id":"int_…","content":"…"}}
{"type":"turn/end","seq":6,"time":…,"data":{"interaction_id":"int_…","status":"completed"}}
```

1. **`seq` 每个会话单调递增**，由唯一写入者分配；`time` 为观测时刻（秒，浮点）。
2. **`data` 里的关联 ID 统一用 `session_id`、`interaction_id`、`task_id`**。Runtime 适配器内部的 `product_session_id` 不改（它用来区分 BoxAgent 会话和模型侧的 thread），但不再写进持久记录。
3. **写入**：沿用现有的 `flush + fsync`；上下文条目沿用 REQ-01 的后台单线程写入，不阻塞对话。

### 3.3 事件种类

| 分组 | 事件 | 来源 | 现在叫 |
|---|---|---|---|
| 业务事实 | `turn/start`、`turn/end` | `ConversationService` | `interaction.started` / `interaction.finalized` |
| | `user/message`、`assistant/message` | 同上 | `message.final`（按 role 区分） |
| | `compaction/summary` | `ContextCheckpointCoordinator` | `context.checkpoint.created` |
| | `task/start`、`task/end` | `ExecutionService` | 只存在于 `runs/index.jsonl` |
| 回合准备 | `turn/prepare` | `InteractionService` | 账本 `turn/prepare`（REQ-02 在此追加 `jev`） |
| 上下文条目 | `context/rebuild`、`context/item`、`context/delete`、`context/replace` | `QwenContextRecorder` | 账本中的 `system/message`、`user/message` 等；删除/替换新增 |
| 请求输入 | `context/request` | 同上 | 新增：生成前有效条目快照、来源和 delta |
| 回复生命周期 | `response/start`、`response/end` | 同上 | 账本 `turn/start` 等 |

**为什么上下文条目不直接沿用 `user/message`**：账本的 `user/message` 是“模型收到的用户 item”，业务的 `user/message` 是“用户说的话”。两者同名会让投影和人工排查都出错。改用 `context/item` 加 `category` 字段（沿用 REQ-01 的 11 个分类）。

**为什么任务明细不进主流**（与 DSH 的差异）：

1. 本机 2 个任务共产生约 150 条明细，其中 `server_event`、`heartbeat`、`rpc_*` 占 55%；
2. 一个会话可能跑几十个任务，混入主流会让“消息列表”这类最常用的投影读取量增加一个数量级；
3. DSH 的工具调用是对话本身；我们的任务是独立后台执行，有自己的生命周期。

所以主流只写 `task/start` 和 `task/end`（带 `task_id`、目标摘要、状态、耗时、明细目录引用），明细留在 `tasks/<task_id>/`。

### 3.4 读写接口

1. 新增 `SessionEventStore`，作为唯一读写入口：`append(session_id, type, data)`、`read(session_id, types=None, after_seq=0)`。
2. 现有 `JsonlSessionStore`（业务事实）和 `JsonlContextLedgerStore`（账本）改为它之上的两个**投影**，对上层接口不变，因此 `MemoryIngestion`、`ContextCheckpointCoordinator`、`ConversationService` 不用改。
3. 读取按 `types` 过滤；最大会话约 1200 条事件，REQ-01 实测全量读取加投影 13 ms，暂不需要索引。

#### 3.4.1 面向上下文展示的请求快照（2026-10-11 补充）

用户参考 DSH 轨迹管理及 [dsh-context](https://github.com/bowenliang123/dsh-context) 的上下文展示。仅合并时间线不足以重建“这一请求到底带了哪些内容”，还需明确有效输入投影：

本地 Product Session 的上下文选择是主契约：持久事实/审计 → 有效 `ContextState` → 每次 `ContextSnapshot` → Runtime adapter 增量同步。Provider 删除回执只是同步状态；本地到期、历史保留与 Checkpoint 覆盖独立定义。现状与契约建议见 [生命周期设计 §0](REQ-02-03-Query参考包生命周期.md#0-当前-product-session-与推荐方案)。

1. `context/item` 带本地条目 ID、provider item ID（获回执后关联）、有序位置、类别、内容来源、`interaction_id`、生命周期 scope。JEV/召回记忆/环境按 [Query 参考包设计](REQ-02-03-Query参考包生命周期.md) 管理。
2. `context/delete` 区分 requested、confirmed、failed；只有 provider 回执才能确认服务端删除。原文和历史事件保留在日志中，当前有效输入投影排除已确认删除项。
3. 每次 `response.create` 记录 `context/request`：本地 request ID、Query 与响应来源、稳定 instructions/tools revision、本次有效条目有序引用、各类估计 token 与相对上次 delta；`response.created` 后绑定 provider response ID。语音自动生成等无法在请求前取样的路径标明采样时机与覆盖限制，不伪造精确快照。
4. `context/replace` / checkpoint 记录被替换范围与来源，区分过期删除、工具文本剪短、历史总结。迁移旧日志无法完整还原时保留缺失/近似标志。
5. 展示层分开“当前/历史请求的有效输入”与“完整审计历史”。宿主视图不能证明服务端没有截断；实际 usage、估计 token 和缓存命中状态分别记录，未知缓存命中不填 0、不由延迟推断。

### 3.5 锁

1. Engine 打开会话时用 `fcntl.flock` 独占 `session.lock`，关闭时释放。
2. 迁移脚本、回放脚本写入前必须拿到同一把锁；拿不到就退出并提示“Engine 正在使用该会话”。
3. 锁文件不作为会话存在的依据（与 DSH 一致）。

### 3.6 迁移

1. **触发**：Engine 启动时检查 `sessions/.migrated-v1`。不存在就执行迁移，也可以用 `scripts/migrate_sessions_v1.py` 手动执行。
2. **步骤**（每个会话）
   1. 读取旧 `events.jsonl`、`context.v1.jsonl`、`context.jsonl`、元数据；
   2. 按 `time` 归并成一条流，重新分配 `seq`；类型按 §3.3 改名，原类型保留在 `data.legacy_type`；
   3. 先写 `session.v1.jsonl.tmp`，fsync 后重命名；
   4. 把 `runs/by-session/<id>/` 指向的任务目录**复制**到 `tasks/`。
3. **校验**：迁移后业务事实条数、账本条数与旧文件逐一相等，否则这个会话不切换，在 `errors.jsonl` 记一条 error。
4. **回滚**：脚本持锁后先把统一流的全部正式历史、绑定、检查点和账本导出到 `conversations-unified-rollback`，再切换标记。原始 `conversations/` 和统一 `sessions/` 均保留；不得删除统一流来回滚，否则会丢掉迁移后的新增消息。

---

## 4. 不做的事

1. **不压缩**：zstd 归档放在 INFRA-04；明文便于现阶段排查。
2. **不改 Jev-Mem 存储**：长期记忆仍是跨会话的独立 Store。
3. **不改 Qwen 运行时目录**：`boxagent/infrastructure/runtimes/qwen/` 零改动，账本仍从 trace 回调取数据。
4. **截图不按哈希存储**：放在 INFRA-04；本机 108 张截图里只有 4 组重复，去重收益主要来自以后的长任务。

## 5. 风险

| 风险 | 应对 |
|---|---|
| 迁移中断导致会话丢失 | 只复制不删除；临时文件加重命名；逐会话校验 |
| 主流变大后读取变慢 | 按类型过滤；实测 13 ms；超过 50 ms 再引入 INFRA-04 的投影缓存 |
| 上下文条目的后台写入与业务事实的同步写入争用 `seq` | `seq` 在写入线程内分配，业务事实的同步写入也提交到同一线程，并等待 fsync 完成 |
| 旧版本降级 | 旧目录保留；新版本不改旧文件 |

## 6. 实施步骤

| 步骤 | 内容 | 验收 |
|---|---|---|
| 1 | `SessionEventStore` 与事件模型；单测（seq、撕裂修复、锁、按类型读取） | 新模块单测通过；不接入生产 |
| 2 | 业务事实与账本改为投影；生产写新流 | 全量测试通过；手动跑一轮文字和语音对话后，消息列表和 `context_snapshot` 与改动前一致 |
| 3 | 迁移脚本与启动迁移 | 本机 12 个会话迁移后逐条校验通过；回滚后旧版本能正常启动 |
| 4 | 任务目录挂到会话下；`runs/` 只读兼容 | 新任务写在会话目录；历史任务仍可在界面查看 |

## 7. 2026-10-12 实施决策

1. 任务明细放在 Session 的 `tasks/` 目录；主流记录任务起止、实际上下文请求和参考移除。任务复制后校验文件内容哈希。
2. 统一流用 `context/*` 类型，原账本类型保留在 `ledger_type`；原 `context_snapshot` API 继续投影。
3. 已有数据手动迁移，要求 Engine 退出；全新安装直接使用统一流。没有旧账本的会话标记来源不完整，诊断 trace 仍可只读回放，不能伪造 provider 历史。
4. 旧目录保留，本轮不删除。INFRA-04 同期实现 gzip 与哈希附件；§4 为最初的数据层划分，不代表最终缺少这两项。
