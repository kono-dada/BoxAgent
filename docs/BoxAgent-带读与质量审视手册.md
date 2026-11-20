# BoxAgent 带读与质量审视手册

用法：本手册按“一次一个文件”的节奏组织。每一节是一个 30-60 分钟的阅读单元，读完再进下一节。每个文件都给出绝对路径、读法与质量观察点。

配套：[代码架构导览](./BoxAgent-代码架构导览.md)（先看结构，再看本手册逐文件）。

---

## 0. 一次审视一个文件时问六个问题

顺序固定，前三个决定“能不能改”，后三个决定“改得起不起”。

1. 这个文件对外的契约是什么？（函数签名、Protocol、数据结构）
2. 它依赖谁、被谁依赖？跨层了吗？
3. 它的失败路径是什么？异常被吞了吗？
4. 单个函数是否超过 60 行、单个类是否超过 20 个方法？
5. 有没有全局可变状态或跨请求共享的缓存？
6. 有什么测试在守着它？没有的话，改动风险由谁承担？

---

## 第 1 单元：入口与装配（约 40 分钟）

### 1.1 `/Users/isaac/rbm_project/BoxAgent/boxagent/entrypoints/engine.py`（48 行）

读法：全文一次读完。

看什么：参数 → Settings → Server 的三段式；`run` 里为什么把两个 import 写在函数内。

质量观察：

1. `run(args)` 与 `main()` 都没有参数与返回类型标注——入口是进程边界，恰恰最该有契约。
2. 函数内 import 是有意的（避免入口阶段拉起语音依赖），但没有任何注释说明，后来者容易“顺手”挪到顶部。
3. 信号处理里 `NotImplementedError` 分支是 Windows 兼容路径，没有测试覆盖。

### 1.2 `/Users/isaac/rbm_project/BoxAgent/boxagent/bootstrap/settings.py`（253 行）

读法：只看 `Settings` 数据类字段 + `load_settings`，跳过每个 `read_*` 的细节。

看什么：所有可调项如何从环境变量进来；哪些有默认值、哪些必须显式给。

质量观察：

1. 环境变量解析集中在一个文件，是好事；但每个 `read_positive_int` 各自写边界校验，重复度高。
2. 默认值散落在解析函数里（例如回合前 `budget=1.5s` 其实不在 settings 里，而在 `TurnPreparer` 构造函数），导致“默认值只有一个来源”这条不成立。

### 1.3 `/Users/isaac/rbm_project/BoxAgent/boxagent/bootstrap/engine.py`（476 行）

读法：只读 `create_application`（`:228-476`），先看对象创建顺序，不读参数细节。

看什么：谁先创建、谁被注入给谁、哪些变量在函数尾部被反向赋给 application。

质量观察：

1. 函数 229 行、10 个参数全部无标注——这是全仓最该先修的一处。
2. 反向赋值：`application.interaction_service.companion_name = ...`（`:425`）、`application.observer = ...`（`:427`）——装配逻辑伸手改运行时对象，类型系统看不见。
3. 双会话存储分支（`:251-270`）让“当前到底用哪套存储”变成运行时才能知道的事。

### 1.4 `/Users/isaac/rbm_project/BoxAgent/boxagent/interfaces/engine/server.py`（288 行）

读法：读 `dispatch`（`:145`）的方法名列表，再读 `_handle_client` 的错误处理。

看什么：34 个方法如何路由；错误如何变成 `ok/error` 响应。

质量观察：

1. 118 行的 if 链，新增方法只能继续加分支，没有注册表。
2. 方法名是裸字符串，前后端没有共享的常量或枚举——打字错误只能靠运行时报“unknown method”。

---

## 第 2 单元：应用与领域主干（约 2 小时）

### 2.1 `/Users/isaac/rbm_project/BoxAgent/boxagent/application/assistant.py`（880 行）

读法：`class` 定义只扫方法名（67 个），然后精读 `submit_text`（`:224`）、`handle_tool`（`:664`）。

看什么：应用层只做编排与转发，判断“哪些逻辑其实属于领域层”。

质量观察：

1. 67 个方法里混着生命周期、快照拼装、Skill 确认、工具路由、相机、游戏播报——职责过载。
2. `handle_tool` 130 行，同时处理任务委托、记忆写入、Skill 安装、通知，四个能力塞一个函数。
3. `context_cache`（`:70`）按 session 缓存且无上限；session 数量增长时不会淘汰。

### 2.2 `/Users/isaac/rbm_project/BoxAgent/boxagent/domain/interaction/service.py`（946 行）

读法：读 `submit_text`（`:216`）与 `_conversation_event`（`:438`）。

看什么：一轮对话的事件如何改变状态；重连与打断如何不串台。

质量观察：

1. 状态机 103 行嵌在一个方法里，四个事件分支共享局部变量，改动容易互相影响。
2. 这个类 34 个方法，同时管连接、麦克风、工具、通知投递——边界已经模糊。
3. 好消息：这里有真实的并发护栏（`toggle_lock`、任务身份比对），不是靠“应该不会并发”的假设。

### 2.3 `/Users/isaac/rbm_project/BoxAgent/boxagent/application/turn_preparation.py`（174 行）

读法：`prepare`（`:44`）全文。这是全仓设计密度最高的函数之一。

看什么：四路并行、各自期限、晚到结果如何被丢弃、包如何截断。

质量观察：

1. 单函数 104 行，做六件事：启动任务、等期限、收集状态、组装 JSON、按字符预算截断、生成报告。
2. `budget` 默认 1.5s（`:29`）与需求文档写的 300–400ms 冲突，且不在 Settings 里——线上行为和文档不一致且不可配。
3. 截断逻辑（`:128-138`）按字段顺序砍字符，砍完可能把一条语义完整的记忆切成半句。

### 2.4 `/Users/isaac/rbm_project/BoxAgent/boxagent/domain/conversation/service.py`（250 行）

读法：`begin_interaction` / `append_assistant_message` / `finish_interaction` 三个方法。

看什么：产品事件如何落盘；sink 如何把消息分发给记忆与 Checkpoint。

质量观察：

1. 这是“唯一真相源”的守门人，实现克制、方法短——是本仓质量最好的文件之一，值得作为改写其他类的模板。
2. sink 采用“先落盘再通知”的次序，正确；但 sink 异常会不会阻断落盘需要单独确认。

### 2.5 `/Users/isaac/rbm_project/BoxAgent/boxagent/domain/execution/service.py`（328 行）

读法：`start`（`:70`）、`_run`、`_cancel`（`:288`）。

看什么：串行队列怎么保证“停止不误伤下一个任务”。

质量观察：

1. 取消时固定原任务身份再比对，是这类代码里最容易写错的地方，这里写对了。
2. 队列状态与快照发布是两处手写，新增状态字段时容易漏发快照。

---

## 第 3 单元：执行链与契约（约 2 小时）

### 3.1 `/Users/isaac/rbm_project/BoxAgent/boxagent/agent/runtime/contracts.py`（41 行）

读法：全文。这是“换模型不用改业务”的全部依据。

质量观察：`ToolCall = Callable[[str, dict], Awaitable[dict]]` 用了裸 `dict`，与 `AGENTS.md` 要求的 TypedDict 不一致；`ComputerUseSession` 里 `process: object | None` 同样丢掉了类型。

### 3.2 `/Users/isaac/rbm_project/BoxAgent/boxagent/agent/harness/request_builder.py`（83 行）

读法：`prepare`（`:44`）与 `prepare_event`（`:62`）。

看什么：用户原话如何变成 Runtime 请求，历史与证据如何拼进去。

质量观察：两个 `prepare` 几乎同构（`:44` 与 `:62`），第三处调用时大概率会再抄一份，应该抽公共编译段。

### 3.3 `/Users/isaac/rbm_project/BoxAgent/boxagent/agent/harness/executor.py`（395 行）

读法：`_execute`（`:135`）与终态判定。

看什么：“工具说成功 ≠ 任务完成”这条不变量落在哪几行。

质量观察：141 行的执行循环里同时有重试、证据校验、终态映射，三者耦合；这是最需要拆、也最需要回归测试的地方。

### 3.4 `/Users/isaac/rbm_project/BoxAgent/boxagent/domain/notification/service.py`（122 行）

读法：`enqueue` / `claim` / `acknowledge` / `retry` 四个方法。

质量观察：四个方法各 10-20 行，状态转移清晰——小而完整的领域服务样板。

### 3.5 `/Users/isaac/rbm_project/BoxAgent/boxagent/core/events.py`（20 行）+ `/Users/isaac/rbm_project/BoxAgent/boxagent/core/states.py`（137 行）

读法：全文。全仓状态发布的唯一出口。

质量观察：`emit` 用 `setattr` + `hasattr` 做字段校验，意味着**状态字段改名不会在类型检查阶段报错，只会在运行时抛异常**。这是“用运行时校验换开发便利”的典型取舍，代价是重构安全性。

---

## 第 4 单元：记忆（约 1.5 小时，只读边界不读算法）

### 4.1 `/Users/isaac/rbm_project/BoxAgent/boxagent/bootstrap/memory.py`（111 行）

看什么：记忆模块由哪些部件组成，为什么都塞在一个返回值里。

质量观察：`memory._service.backend`（`:104`）直接穿透私有字段，说明公开接口没覆盖“视觉后端”这个需求。

### 4.2 `/Users/isaac/rbm_project/BoxAgent/boxagent/application/memory.py`（311 行）

看什么：`MemoryModule` 的公开方法（26 个）与 `DisabledMemoryModule`（`:234`）的镜像实现。

质量观察：两个类各写一遍 21 个签名，没有 Protocol 约束——任何一侧改签名都不会被检查出来。

### 4.3 `/Users/isaac/rbm_project/BoxAgent/boxagent/infrastructure/memory/jev_mem/client.py`（220 行）

看什么：主进程与记忆子进程之间的协议（请求、超时、降级）。

质量观察：这是“重依赖隔离在子进程”的实现点，是资源占用治理的关键一环，值得单独理解。

### 4.4 `/Users/isaac/rbm_project/BoxAgent/boxagent/infrastructure/memory/jev_mem/UPSTREAM.md`

读法：只看这个文件。43 个 vendored 文件不要逐个读。

---

## 第 5 单元：模型适配器（按需，各 30 分钟）

| 文件 | 行数 | 看什么 |
| --- | --- | --- |
| `/Users/isaac/rbm_project/BoxAgent/boxagent/infrastructure/runtimes/qwen/realtime.py` | 1332 | `receive`（`:487`）事件分发；WebSocket 侧的 response 生命周期 |
| `/Users/isaac/rbm_project/BoxAgent/boxagent/infrastructure/runtimes/qwen/aoq.py` | 730 | `AoqRealtimeSession`（`:498`）如何在子类里覆写传输，`_load` 如何加载 SDK |
| `/Users/isaac/rbm_project/BoxAgent/boxagent/infrastructure/runtimes/codex/app_server.py` | 708 | `run_codex_turn`（`:440`）的 JSONL 协议与进程生命周期 |

三个文件共同的质量问题：都是“协议 + 状态机 + 生命周期”混在一个类里，且都在 700 行以上。

---

## 第 6 单元：门禁与测试（约 40 分钟）

| 文件 | 看什么 |
| --- | --- |
| `/Users/isaac/rbm_project/BoxAgent/tests/test_architecture.py`（302 行） | 20 条依赖规则怎么用 AST 表达；哪条规则最容易误伤重构 |
| `/Users/isaac/rbm_project/BoxAgent/tests/test_logging.py` | 如何扫描全源码禁止静默 `except` |
| `/Users/isaac/rbm_project/BoxAgent/tests/test_executor.py`（1143 行） | 终态与证据校验的回归样本 |
| `/Users/isaac/rbm_project/BoxAgent/tests/test_voice_task_sequence.py`（265 行） | 语音插话、任务串线的最小复现 |

---

## 7. 好的 Python 应用如何优化代码质量

四条原则，按投入产出排序。

### 7.1 类型只花在边界

结论：标注覆盖率不是目标，**跨模块签名全覆盖才是目标**。

1. 必标：公开函数、Protocol、dataclass 字段、工厂与回调。
2. 不必标：局部变量、测试函数、一行的内部实现。
3. 开启方式：不要全仓开 `strict`，而是一层一层加 `reportMissingParameterType`，每次只扩一个目录。

本仓库现状：1,620 个函数中参数与返回齐全的仅 15%；`bootstrap/` 与 `application/` 是最大空洞。

### 7.2 一个装配点，其余靠注入

结论：**谁能 new 具体实现**这件事，决定了重构成本上限。

1. 只允许 `bootstrap/` 创建生产对象。
2. 应用层与领域层只接收已构造好的依赖。
3. 用测试表达这条规则，而不是写进文档。

本仓库现状：这条守得很好（`tests/test_architecture.py` 20 项全过）。

### 7.3 用不变量写测试，不用文件写测试

结论：测试的组织单位应该是**不能破坏的规则**，而不是“每个模块一个文件”。

1. 串行队列不串台、失败不伪造完成、事件不可修改——每条都该有一个专门的测试。
2. 这类测试比“覆盖率”更能防止架构腐化。

本仓库现状：667 个用例、52 秒跑完，回归成本已经很低，属于良性资产。

### 7.4 错误策略显式化

结论：**每一种失败都必须有名字**。

1. 禁止裸 `except`（本仓库已用测试守住，实测 0 处）。
2. 失败返回结构化终态（`blocked` / `failed`），不要抛到用户面前说“出错了”。
3. 宽泛捕获必须记录并上抛。

---

## 8. 好的 Python 应用如何优化资源占用

分四类。每类给“原则 → 本仓库证据 → 动作”。

### 8.1 内存

| 原则 | 本仓库证据 | 建议动作 |
| --- | --- | --- |
| 重依赖放子进程，主进程保持瘦 | Jev-Mem、Qwen MLX、Codex 各自独立子进程（`create_subprocess_exec` 6 处 + `JevMemWorker`） | 保持；新增重依赖（如本地向量模型）继续走子进程 |
| 大对象按内容哈希共享，不复制 | 任务截图走 SHA-256 附件存储，轨迹只存引用 | 保持；视觉记忆的帧也应按同法去重 |
| 缓存必须有界 | `context_cache` 按 session 但不设上限；`provider_items` 随会话增长 | 给 cache 加 LRU 或上限；确认 ledger 在会话切换时清理 |
| 小对象密集时用 `__slots__` / `dataclass(slots=True)` | 全仓 `slots=True` 为 0，`@dataclass` 91 处 | 只对“每会话成百上千个”的对象加，不要全量加 |
| 大文件用流式读 | 微信库快照、`WAL` 用 `read_bytes()` 全量载入 | 超过 10MB 的读取改为分块或 mmap |

### 8.2 CPU

| 原则 | 本仓库证据 | 建议动作 |
| --- | --- | --- |
| 事件循环里禁止阻塞 | `asyncio.to_thread` 使用 86 处 | 保持；新代码遵循同一惯例，并加 lint 规则 `ASYNC240` 守住 |
| 禁止忙等 | 有 2 处 `ASYNC110`（async busy-wait） | 改成 `Event` 或 `Condition` |
| 纯函数结果缓存 | 仅 2 处 `lru_cache` | 模型目录、SOUL 解析这类不变输入可加 `lru_cache` |
| 轮询间隔可配 | 屏幕摘要默认 15s，可配 | 保持；CPU 敏感场景（如电池供电）应允许调大 |

### 8.3 IO 与持久化

| 原则 | 本仓库证据 | 建议动作 |
| --- | --- | --- |
| 每条记录都 fsync 是性能陷阱 | `qwen_ledger.py:68` 有注释明确说明 fsync 毫秒级成本，trace 路径刻意不做 | 把这条取舍写成文档规则：**审计级数据才 fsync，诊断级数据批量刷** |
| 配置写用临时文件 + `os.replace` | 已有多处（`memory_profile`、`skill_repository`、wechat） | 保持；补一个统一工具函数，避免每处手写 |
| 日志必须轮转 | `bootstrap/logs.py` 有轮转与清理 | 保持；确认长跑场景下总目录有上限 |

### 8.4 启动

| 原则 | 本仓库证据 | 建议动作 |
| --- | --- | --- |
| 入口只拉起必需依赖 | `engine.py` 把语音/引擎 import 放在函数内 | 给这些函数内 import 补一行注释说明原因 |
| 重组件预热而不是首用加载 | Jev-Mem Worker 启动时预热 | 保持；MLX 视觉模型按需启动，避免常驻内存 |
| 用测量而不是感觉 | 未在 CI 中度量启动耗时 | 加 `python -X importtime` 基线，超过阈值告警 |

### 8.5 三个可直接用的测量命令

```bash
# 1. 看启动时哪个 import 最贵
python -X importtime -c "import boxagent.bootstrap.engine" 2>&1 | sort -k2 -rn | head -15

# 2. 看常驻内存构成（Engine 跑起来后）
python -c "import psutil,os; p=psutil.Process(os.getpid()); print(p.memory_info())"

# 3. 找内存峰值对应的分配点
python -m tracemalloc  # 或在代码里 tracemalloc.start(); snapshot 对比
```

---

## 9. 这套代码里最值得做的六件事

| 序 | 动作 | 位置 | 预期收益 |
| --- | --- | --- | --- |
| 1 | 给 `create_application` 拆函数并补标注 | `bootstrap/engine.py:228` | 全仓最大的单点认知负担 |
| 2 | `dispatch` 改成注册表 + 方法名常量 | `interfaces/engine/server.py:145` | 新增 API 从“改 if 链”变成“加一个 handler” |
| 3 | `MemoryModule` 定义 Protocol | `application/memory.py` | 消除 21 个方法的签名漂移风险 |
| 4 | 状态发布改成显式字段描述 | `core/events.py` | 字段改名能在类型检查阶段发现 |
| 5 | 回合前 `budget` 进 Settings 并与文档对齐 | `turn_preparation.py:29` | 线上行为可配、可解释 |
| 6 | 给缓存与 ledger 加清理策略 | `assistant.py:70`、`qwen_ledger.py` | 长跑内存不再单调增长 |

前两项属于“改完立刻降低所有后续改动成本”，建议先做。
