# REQ-02/03 Query 参考包与生命周期（设计稿）

> 状态：2026-10-12 生产已接入；REQ-03/04 通过功能验收。REQ-02 两轮配对 A/B 已完成，用户接受实测延迟增量，JEV 默认开启，超时仍跳过；见 [实施记录](2026-10-12-陪伴上下文实施验收.md)。
> 2026-10-11 用户补充：JEV 决策参考与每次召回的记忆只服务当前 Query；需要兼顾 KV cache，并为后续上下文展示记录实际构成。
> 2026-10-11 范围澄清：先设计 **本地 Product Session 的有效上下文**，不是以阿里云删除事件为主设计。Provider 承载并同步本地选择的上下文；此处保留设计时的现状基线，当前实现以实施记录为准。
> REQ-02 的意图模型评估、准备预算与宿主控制时机基线仍见 [README](README.md) §3。

## 0. 当前 Product Session 与推荐方案

### 0.1 当前已有上下文，不是每轮从零请求

本地现在有三份相关记录，职责不同：

| 文件 / 对象 | 当前内容 | 是否直接作为每轮完整输入 |
|---|---|---|
| `conversations/sessions/<ses_id>/events.jsonl` | 用户/助手 `message.final`、Interaction 起止等业务事件 | 否；是正式对话的持久来源 |
| 同目录 `context.jsonl` | 历史范围 Segment 与结构化 Checkpoint | 否；冷恢复时选最新 Checkpoint |
| 同目录 `context.v1.jsonl` | 系统规则、角色、工具、画像、记忆、环境、消息等发送账本 | 否；供观测，不被当作正式历史全量重放 |
| 活跃 `QwenRealtimeSession` / AOQ 连接 | 服务端已创建的上下文 items 与连续回复 | 同一连接中继续沿用已建立上下文，宿主增量追加 |

`InteractionService._connect` 会复用满足输入模式的现有连接；首次连接/重连读取 `ConversationService.runtime_context`，取最新 Checkpoint 和覆盖范围之后的正式消息，由 `RuntimeContextProjector.restore_context` 按预算选择原生消息，再用 `inject_history` 注入 Qwen。当前默认历史预算 24000 字符，Checkpoint 触发阈值 18000 字符、单批源文本上限 32000 字符。这些都不是模型 token 窗口。

Checkpoint 目前写入本地、用于之后冷恢复；没有看到它生成后立即替换活跃 Qwen 上下文的接线。因此，冷恢复的正式历史视图与同一热连接累计的参考包视图现在可能不同。Product Session ID 也不是阿里云识别的持久会话 ID：本地 ID 用于宿主关联，恢复依靠主动发送历史。

### 0.2 本次实查样本（2026-10-11）

活动 Product Session：`ses_8a1771134db940d7`，标题“上下文测试01”。Engine 当前 voice=off，以下是 **2026-10-10 12:07–12:11 最后记录** 的上下文，不代表此刻仍有活跃 Qwen 连接。

| 内容 | 本地记录 |
|---|---|
| 正式业务事件 | 16 条：4 次 started、8 条正式消息、4 次 finalized |
| 正式消息 | 4 user + 4 assistant，共 140 字符 |
| Checkpoint | 0；本机 12 个 Product Session 的 `context.jsonl` 均无 Checkpoint 记录 |
| 模型内容账本 | 37 条事件，其中 20 条内容条目；估算 6045 tokens、16651 字符 |
| 下一次冷恢复 | 8 条正式消息全部进入历史，共 140 字符；不重放旧记忆包/环境包 |
| JEV 操作意图参考 | 尚未接入当前前台请求 |

最后记录的模型内容构成（含第四轮已经生成的助手答复；token 是本地估算）：

| 类别 | 份数 | 估算 tokens | 占比 |
|---|---:|---:|---:|
| 系统规则 | 1 | 715 | 11.8% |
| 角色设定 | 1 | 531 | 8.8% |
| 工具定义 | 1 | 948 | 15.7% |
| 稳定用户画像 | 1 | 169 | 2.8% |
| 累计记忆包 | 4 | 3299 | 54.6% |
| 累计环境包 | 4 | 252 | 4.2% |
| 用户消息 | 4 | 8 | 0.1% |
| 助手回复 | 4 | 123 | 2.0% |

同一热连接记录的顺序是：

```text
系统规则 + 赫萝角色 + 工具 + 稳定画像
记忆包1 + 环境包1 + Q1“？”       + A1
记忆包2 + 环境包2 + Q2“可以”     + A2
记忆包3 + 环境包3 + Q3“？”       + A3
记忆包4 + 环境包4 + Q4“好难受啊” + A4
```

记忆包为 `<boxagent_memory_context>` 包裹的 JSON，含 `profile_version / profile / evidence / narrative`；这 4 份包每份都有 3 个画像字段、5 条 evidence、0 条 narrative。稳定画像又被逐轮重复。样本还召回了“？”“可以”等短输入事件；这些来自当时的 `magma_fallback` 数据，不能据此推断现在阿里云决策模型的记忆准入质量。

### 0.3 推荐的本地模型：事实日志 → 有效上下文 → Provider 同步

**Product Session 负责决定用哪些上下文，阿里云负责沿着这份上下文推理。** 正式事实与参考包审计都保留在日志；增加可重算的有效上下文投影，每次请求固定一份引用快照。过期意味着退出有效投影，不是物理删除历史记录。

```text
Product Session 的持久事实 / 参考包审计
              ↓ 根据 scope、版本、覆盖范围与预算选择
本次有效上下文：稳定规则/画像 + Checkpoint/近期正式历史 + Q当前 + 当前参考包
              ↓ 与已有连接的条目映射和 cursor 比较
Provider adapter：热连接发送增量；新连接发送完整选定上下文
              ↓
Qwen 连续推理；请求快照、发送结果及服务端回执反向进入审计
```

推荐最小契约（字段名为设计建议，尚未实现）：

| 对象 | 最小字段与职责 |
|---|---|
| `ContextItem` | `item_id, source_event_ids, category, role, content_ref, scope, owner_interaction_id, revision` |
| `ContextState` | 当前稳定版本、Checkpoint 覆盖 cursor、近期正式消息引用、当前 Query 参考包引用、revision |
| `ContextSnapshot` | `request_id, interaction_id, context_revision, ordered_item_ids, inclusion_reason, estimates`；生成前固定 |
| `RuntimeBinding` | 本地上下文版本/cursor、provider generation、local item → provider item 映射、同步状态 |

1. 稳定规则、角色、工具与画像只保留一个有效版本。画像真实变化时显式产生版本更新，逐轮参考包不再复制整份画像。
2. 业务 user/assistant 历史继续存在；长历史由 Checkpoint + 近期完整 Interaction 表示，旧原文仍在审计。正常任务结果保留可解释的执行事实；目前完整工具调用/结果主要在账本和任务记录中，需显式投影后才能成为可恢复历史，不能假定已有。
3. 每个 Query 的 JEV/召回/环境绑定 Interaction。下一 Query 时在本地将旧包标为 expired，当前包为 0 或 1 份。相同 Query 的工具续答沿用该包；新 Query 的“继续”等短句仍保留正式历史，可按本次 Query 重新选取必要证据，不继承上轮意图概率。
4. Provider adapter 从本地有效状态计算同步差异。活跃连接复用已有上下文；重连恢复同一有效状态，避免每轮重建空连接。服务端删除只是 adapter 使承载内容符合投影的一种手段，不负责决定到期策略。
5. 区分 `local_expired` 与 `provider_removed`：本地过期后，服务端旧包若仍在，必须显示不同步、处理重试/重建，不能仅改本地界面就声称实际输入已剪枝。正常下一 Query 的生成需在约定同步条件满足后触发。
6. 展示提供三种视图：本次选定有效输入、实际发送/承载状态、完整审计历史。当前按最近 rebuild 累计的账本不能冒充未来的有效投影；需要保留其原有观测含义。

### 0.4 先后顺序

设计顺序为：先做本地 `ContextItem / ContextState / ContextSnapshot` 契约与投影、明确正式历史恢复规则；再让现有 Qwen adapter 增量同步该投影，最后接入 JEV。INFRA-01/02 与 REQ-02/03 共享这套契约，目录迁移不应替代上下文选择规则的设计。

## 1. 放置位置

建议按以下逻辑顺序构造输入；这是客户端发送顺序，服务端内部模板不可见：

```text
稳定 instructions / 角色 / tools / 稳定用户档案
有效对话历史（user、assistant、实际工具结果、必要 checkpoint）
当前 Query（文字 user item，或语音提交后服务端生成的 user item）
本轮参考包（JEV 意图估计 + L2 召回记忆 + 当前环境）
→ 宿主 response.create
```

1. 不把每轮 JEV 概率、召回结果、时间戳写进 `session.instructions`，避免改动最早的前缀。
2. 本轮参考合并成一条 system item，绑定 `interaction_id` 与当前 provider user item。稳定规则说明它是参考数据，不能覆盖用户请求；JEV 提供概率与简短证据时才附上证据，不虚构理由或注入长推理。是否调用 `run_task` 始终由 Qwen 判断。
3. 文字和语音采用同一准备/提交入口：当前用户输入定稿后并行准备，300–400 ms 总预算，确认 user item 后追加本轮包，再触发回答。预算内没有任何可用内容时允许零份包。
4. 参考包结构包括 `interaction_id`、`scope=current_query`、子项来源与状态；召回附原始 memory ID / 版本，环境附采集时间。超时/失败/跳过只写审计事件，不把上轮内容当本轮成功结果。

## 2. 有效期与本地过期

| 内容 | 生命周期 |
|---|---|
| instructions、角色、工具、稳定档案 | Session 中稳定；真实配置变化时显式更新 |
| 用户/助手消息、真实工具结果 | 按正常历史策略保留；需要时另行总结 |
| JEV 意图参考、本次 L2 召回、当前环境 | 当前 Query 有效；下一 Query 将旧包从本地有效投影排除，创建新包并同步 Runtime |
| 同一 Query 的同步工具续答 | 继续使用当前包，不在每个 `response.done` 都删除 |
| 后台任务完成通知 | 独立通知输入；不继承旧 Query 的意图/环境包，不为通知重新估计操作意图 |

1. 本地有效上下文中，本轮参考包 **0 或 1 份**，不得存在已过期包。历史参考原文保留供审计；长期记忆 Store 与稳定档案不受召回副本到期影响。Runtime 承载状态须另外同步，不能只过滤展示。
2. Qwen adapter 保存 `conversation.item.created` 返回的真实 ID。删除后等 `conversation.item.deleted` 回执，再更新 provider 同步状态；本地到期按 Query 规则决定，不等同于这个回执。超时/失败记录状态并进入重建/重试策略，不能假报服务端剪枝成功。
3. 新输入、打断、工具回传和通知通过同一串行协调器处理。取消旧准备任务，晚到结果校验 `interaction_id`；旧 Query 的工具续答不得在新 Query 上重新触发。后台任务可继续运行，不因参考包到期而取消。
4. 重连时从业务历史恢复，不恢复过期参考包。历史总结应以对话/执行事实为输入，排除 JEV 估计和过期环境包；必要事实通过回答、工具结果或明确记忆写入留存。
5. 删除参考包不等于从模型中消除其一切影响：助手已引用的事实仍在正常历史里。验收检查包是否退出有效输入，不用“模型不再记得任何旧事实”作为代理。

## 3. KV cache 边界

标准前缀 KV 复用要求此前 token 与顺序一致。以一轮对话为例：

```text
上轮：稳定前缀 | 历史 | Q1 | 参考包1 | A1
下轮：稳定前缀 | 历史 | Q1 | A1 | Q2 | 参考包2
                         ↑ 首个变化位置
```

删除包1后，最多保留首个变化位置之前的共同前缀复用机会；A1 虽然文字未变，其上游已变，标准 KV 不能直接沿用。将临时包放尾部可以尽量延长共同前缀，不能保证所有对话历史的 KV 完整保留。同一 Query 内工具续答不删除包，有利于保持本 Query 的前缀。

阿里云 [Context Cache 文档](https://help.aliyun.com/zh/model-studio/context-cache) 描述 Chat Completions / DashScope / Anthropic 兼容接口的前缀缓存；不能直接把其中 `cache_control` 或计费规则视为 AOQ Realtime 的能力。当前 [Realtime 服务端事件](https://help.aliyun.com/zh/model-studio/server-events) 的 usage 列出输入/输出 text/audio tokens，没有承诺 cached tokens 分项。

因此验收分开记录：实际删除回执、上下文 token 估计/真实 usage、首响应延迟；服务端没有缓存字段时标记 `cache_hit=unknown`。低延迟与输入 token 减少都不能单独证明缓存命中。若将来要求完整历史缓存与每 Query 独立临时输入，需验证服务端是否支持不进入持久历史的请求级上下文；目前未确认，不能作为实现前提。

## 4. DSH 对照与上下文展示

核对本机已安装 `@deepseek-ai/dsh-compaction-tool-result-pruner` 0.2.0-rc.2 的 README 与实现：

1. 上下文压力/溢出触发后，截短超大 `tool/result` 的文本，默认超过 8192 字符时保留前 4096、后 1024，加中间剪除标记；图片/结构化块及工具关联仍保留。不按 Query 自动删除 JEV/记忆参考。
2. 独立的 `compaction-basic` 可将旧历史范围替换为总结，保留近期尾部并维持工具调用/结果配对。这与本需求的确定性到期删除是不同策略。
3. 原事件留在 append-only 日志，通过替换事件表达模型有效输入变化。BoxAgent 同样应保留历史参考包原文与删除事件，供复盘使用。

用户指定的 [dsh-context](https://github.com/bowenliang123/dsh-context) 可参考其每次请求上下文构成、增减趋势与历史回看；它是展示插件，剪枝策略由 Harness 实现。已核对的 [assemble.ts](https://github.com/bowenliang123/dsh-context/blob/2c8095c34da3fcef729a01437d2df65c83e08499/src/client/assemble.ts) 在响应落地前快照请求输入，并对缺失历史标记近似重建。

INFRA-02 需提供：`context/item`、删除 requested/confirmed/failed、每个 `response.create` 的 `context/request` 有序条目引用、对应 JEV/记忆来源、当前有效状态和 delta。界面分别显示“本次有效输入”和“历史审计”；无法确认服务端截断时明确显示宿主已发送视图，不冒称服务端完整输入。

## 5. 2026-10-11 AOQ 协议实验

脚本：[qwen_context_lifecycle.py](../../scripts/experiments/qwen_context_lifecycle.py)。独立会话，模型 `qwen3.8-omni-flash-realtime`，关闭麦克风、播放与工具，只用合成文字。

1. `create_response:false` 被回显；当前 user 后创建 system 参考包，宿主触发回复，Qwen 正确复述包中代号。
2. 本次 provider 返回的 item ID 与客户端预设 ID 不同；按实际返回 ID 删除，收到 `conversation.item.deleted`。
3. 删除后第二轮正常回答“一加一等于二”。证明尾部注入与删除链路可用，不证明缓存命中，也不证明所有模型/所有消息类型都支持删除。
4. AOQ 的 `response.done` 在短观察窗内可能不到；以 transcript done 判断实验文本完成，若收到实际 usage 才记录，不补造用量。
5. 仓库脚本复跑通过（2026-10-11）：`injection_after_user=true`、`delete_acknowledged=true`，两轮分别正确回答代号与算术题；原始非音频事件保存在本机 `/tmp/boxagent-context-probe/reviewable.jsonl`，不作为持久验收附件。

## 6. 推进范围与验收

1. 先确认 §0 的 Product Session 契约；INFRA-01/02 把当前有效条目、变更与请求快照字段落实，避免面板只得到累计日志。
2. REQ-04 控制回复时机生产化后，REQ-02 的注入与 REQ-03 的到期删除一起接入；不能上线一个持续堆积 JEV/记忆包的中间版本。
3. 用多 Query、同 Query 工具续答、打断/取消、晚到准备、删除失败、重连与任务通知验证生命周期。当前上下文旧包数为 0，本轮包数 ≤1；当前 Query 的准备先于首次生成。
4. 保留 README 的意图对照评估和同场景上下文占比 <15% 指标；占比不是唯一门槛，小输入下即使仅一份参考包也可能超过 15%，须同时报告绝对 token 与份数。
5. 单独报告延迟与 cache 可观测性；不得把“完整 KV cache 保留”写成已兑现能力。
