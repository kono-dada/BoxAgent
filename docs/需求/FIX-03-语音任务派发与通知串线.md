# FIX-03：语音任务派发与通知串线

状态：本地收敛与离线回归完成；真实 AOQ 文字输入及两次计算器执行通过；原六轮完整音频、多人全双工及完整通知播报仍待验收。历史验证记录保留，当前逻辑见末尾“决策等待与补丁收敛”。

## 复现与证据

用户报告科学计算器请求只得到口头承诺，没有启动。核对本机 Session `ses_8a1771134db940d7` 与 Qwen trace（2026-10-12，Asia/Shanghai）：

| 时间 | 事实 |
|---|---|
| 21:55:42 / 21:55:44 | “快吗？”与“如果你用 Z V 的话”分别请求响应，首个响应确认尚未到来 |
| 21:55:46 | 实际派发任务 `983ed8a8133d`，目标为“如果你用 Z V 的话” |
| 21:55:54 | 后台以普通文字询问 Z V 含义，0 步操作；结果缺少终态 JSON，被判 `invalid_result` |
| 21:55:56 | 科学计算器请求落盘，意图参考 `p_action=0.96`，并未被决策模型拒绝 |
| 21:55:57 | 旧函数调用结果晚到，provider 返回 `Unknown function call id`；旧任务通知与新请求在约 118 ms 内各触发一次响应 |
| 21:56:00 | 回复承诺重新计算，却无 `run_task`；该交互被提前记作成功聊天，未创建科学计算器任务 |

## 修复边界

- 宿主控制模式下，等待 `response.created` 后才允许下一次生成请求；用户插话取消准备时，仍保留待确认来源，避免队列错位。超时关闭未知连接，不继续复用。
- 响应携带原始 Query ID，Interaction 层只绑定同一个用户回合；过期响应与工具调用不能落到新请求。
- 通知等待语音转写、准备和当前回复完成，等待时释放投递锁；临时参考删除过程中用户开始输入时重新等待。
- 函数结果及时确认，旧 Query 的结果不再补发到新回合；异步任务接受后不生成多余语音回复。
- 已绑定当前 Query 的具体执行承诺遗漏 `run_task` 时修复一次，并从 Product Interaction 获取原文。provider 后续补来的真实调用不能重复派发。
- 回复文本按 response ID 保存，不能从另一个回复借用文本判定执行承诺。
- 前台模型指令区分速度/模型讨论与操作命令。后台忽略终态 JSON 的普通澄清可整理一次结构化结果，整理调用无工具；继续校验已有观察编号，未证明完成不能变成成功。

保留原始 Session、任务日志和截图。本次没有操作计算器、调用真实模型、修改记忆数据或重启正在运行的应用。

## 验证

新增回归覆盖迟到响应确认、插话取消、通知与新请求竞争、科学计算器原文派发、过期响应拒绝、跨回复文本隔离、迟到真实调用不重复派发、澄清格式整理与假成功拒绝。

使用 Python 3.12.12 完成仓库全量检查：

- `UV_PYTHON=3.12.12 UV_CACHE_DIR=/tmp/uvcache uv run --with pytest python -m pytest -q`：344 passed、12 skipped、3 subtests passed。
- `uvx --from pyright==1.1.414 pyright`：0 errors、0 warnings。
- `git diff --check`：通过。

真实 AOQ 任务稳定性仍需重启后的连续语音复验，离线回归不作为真机验收。

## 2026-10-13 真实链路复验

使用生产 AOQ / Qwen `qwen3.8-omni-flash-realtime`、Product Interaction、ExecutionService、NotificationOutbox 和真实决策参考，测试数据隔离在 `/tmp/boxagent-fix03-live-20261013/`；未采集现场麦克风，未写真实长期记忆。

- 普通上下文对照通过：五轮包含命名、名字召回、情绪聊天、休息及操作请求；名字保留、聊天无任务、操作调用和参考删除回执均通过。
- 三轮真实 AOQ 文字输入派发竞争通过（round-15/16/17）：旧任务在科学计算器请求准备时返回；新任务只启动一次，目标及 interaction ID 对应原始输入，速度/模型讨论未派任务，无未知函数 ID。其中一轮延迟处理首个回复确认 0.9 秒。
- 真实执行器测试发现额外误触发：回复“当然可以，后台正在处理计算器的操作，你随时可以继续和我聊。”被原来的“正在处理”字符串规则当成新承诺，额外排入聊天任务。已在本地收紧规则：进度陈述不触发，只识别第一人称的执行承诺；新增三条进度陈述回归样本。该补丁尚未提交或推送。
- 补丁后两轮真实 AOQ 文字输入竞争通过（round-22/23），其中一轮延迟首个回复确认；将最后一条输入改为“我可以继续和你聊吗？不要操作或取消任务”，未产生额外任务。全量 pytest 仍为 344 passed、12 skipped、3 subtests passed；Pyright 当前配置范围内 0 errors、0 warnings。
- 外部合成音频注入只成功转写前两句，后续计算器语句在多次测试中没有产生 ASR，不能算连续语音通过；另有参考写入确认超过原有 1.5 秒期限的失败。未通过放宽期限绕过确认。
- 实际 DeepSeek/Codex Runtime 已启动计算器任务，但 Computer Use 返回 `-10005: noWindowsAvailable`，未验证计算完成。测试发现额外聊天任务后主动停止该轮。最后一次重测在 AOQ Token 分配阶段返回 HTTP 403 `AccessDenied.Unpurchased`；未切换模型或付费配置继续调用。

派发报告位于 round-22/23 的 `report.json`，完整事件和失败记录保存在上述临时目录。当前结论：已覆盖并通过回复来源/通知竞争场景，补派发进度误判已本地修正；不能声称所有连续语音和真实操作已经稳定。

## 2026-10-13 固定回归用例 A：进度回复误派任务

用例 ID：`FIX-03-progress-is-not-a-new-task`。原始任务、聊天输入及真实 Qwen 回复保存于 [voice_task_progress.json](../../tests/fixtures/voice_task_progress.json)，无需临时日志或在线模型即可重放。

复现顺序：计算器任务仍在后台执行时，用户询问“我可以继续和你聊吗？不要操作或取消任务”；伙伴回复“当然可以，后台正在处理计算器的操作，你随时可以继续和我聊。”。预期不新增或排队任务，原任务继续执行，聊天交互正常完成且不关联新的 task ID。

这是后续真实验收发现的独立故障的最小复现，不能单凭这两轮声称最初“科学计算器第二次任务没起来”的连续对话已经覆盖。

- 自动回归入口：[test_live_progress_reply_does_not_queue_chat_as_a_task](../../tests/test_turn_preparation.py)。使用真实 Product Interaction、ExecutionService 和 Qwen 宿主流程，只替换网络连接与执行器。
- 原始回复加两种进度说法，分别覆盖 provider 正常结束和 `audio_done` 收尾，共六个组合。检查任务队列、原任务身份及运行状态、输入回合释放和持久化交互终态。
- 将判定函数临时替换为修复前版本，六个组合均因多排入一个任务而失败；恢复修复版本后六个组合全部通过。
- 同一 fixture 已接入 [真实 AOQ 上下文验收清单](../../scripts/experiments/companion_context_acceptance.py)，检查聊天无 `run_task`；该脚本使用工具替身，后台任务持续运行由上述离线集成测试验证。新增的在线用例尚未重跑。
- 本次全量验证：pytest `345 passed, 12 skipped, 9 subtests passed`；Pyright 当前配置范围内 `0 errors, 0 warnings`。

离线重放命令：

```bash
UV_CACHE_DIR=/tmp/uvcache uv run --with pytest python -m pytest -q tests/test_turn_preparation.py -k live_progress_reply
```

## 2026-10-13 固定回归用例 B：连续插话后第二个任务启动

用例 ID：`FIX-03-second-task-after-interruptions`，输入及回复来源记录在 [voice_task_sequence.json](../../tests/fixtures/voice_task_sequence.json)，自动入口为 [test_second_task_survives_six_turns_and_old_notification](../../tests/test_voice_task_sequence.py)。

使用同一个 Product Session、同一个 Qwen 宿主对象连续重放六轮：

| 顺序 | 用户输入 | 注入的故障条件 |
| --- | --- | --- |
| 1 | 操作本地计算器，十加十 | 启动第一个任务，执行器暂不结束 |
| 2 | 快吗？ | 回复确认正常或迟到；下一轮语音打断尚未完成的回复 |
| 3 | 如果你用 Z V 的话 | 澄清回复；注入上一轮迟到的工具调用，检查不会派给这轮 |
| 4 | 打开科学计算器，二的五次方加根号四四十二 | 准备本轮参考时让旧任务结束，并投递旧工具结果及旧任务通知；使用原报告中的“我重新来”回复，故意不发 `run_task` |
| 5 | 有可能出问题了 | 第二个任务仍运行时继续插话；使用原报告的“我这就去盯着计算器”回复 |
| 6 | 有可能出问题了，别急，哈哈哈 | 第二个任务仍运行时继续聊天；增加用例 A 的真实进度回复作为压力变体 |

这是一组受控事件重放，不是原始音频或完整 provider trace 的逐字节回放。用户输入保留原始转写，“四四十二”也不做预先纠正；第 1、3 轮回复为合成替身，第 4、5 轮来自原始报告，第 6 轮刻意组合后续真实故障回复。为控制旧通知落在新请求准备期间，第一个任务的结束时间和阻塞结果由替身控制，不能算真实计算完成。

检查真实 Interaction、ExecutionService、TurnPreparer、持久化 NotificationOutbox 与 Qwen 接收/派发流程：

- 只启动第 1、4 轮的任务，目标和 interaction ID 对应各自原始输入。
- 第 4 轮遗漏调用后补派发一次；再注入迟到的真实 `run_task`，也不重复执行。
- 第 2、3、5、6 轮不排任务；旧确认、旧工具结果和通知不占用科学计算器的回复来源。
- 第 4 轮不提前记为成功聊天，后续两轮聊天正常完成且无 task ID；第二个任务仍运行，任务队列为空。
- 旧通知仍关联旧任务，等待用户回合结束后作为独立 notification 回复投递。

正常/迟到确认 × provider 正常结束/`audio_done` 收尾，共四个组合通过。负对照中只关闭第 4 轮的漏调用补派发，其余代码保持不变，四个组合全部在等待第二个执行器启动处超时，证明长用例能检测原始“口头承诺后第二个任务没启动”的故障机制。这不等价于所有原始故障代码或实际语音链路都已复现。

加入连续场景后的全量验证：pytest `346 passed, 12 skipped, 13 subtests passed`；Pyright 当前配置范围内 `0 errors, 0 warnings`；`git diff --check` 通过。

离线重放命令：

```bash
UV_CACHE_DIR=/tmp/uvcache uv run --with pytest python -m pytest -q tests/test_voice_task_sequence.py
```

## 2026-10-13 用户复验：状态问答又被补派发

18:36:23 用户问“你现在在干嘛呢？”，Qwen 实际回复“正在帮你打开计算器。”。原任务 `d8f9be7513d3` 仍在运行，宿主的执行承诺正则却在进度句中匹配到“帮你打开”，额外排入目标为状态问题的任务 `79e35feea482`。原计算器任务成功；额外聊天任务没有调用工具，输出 `completed` 与空 `evidence_steps`，被原有校验判为 `missing_evidence`，展示“缺少完成依据，请检查目标应用后重试”。

这个错误不是 FIX-04 的原生观察记录缺失：它属于进度误派发的另一种句式。对应日志发生在 FIX-04 补丁重启前，但当时本地的进度规则也仍能匹配该句。

修复按句子/分句过滤“正在、还在、仍在、已经”等进度或完成陈述，再判断执行承诺；保留后续独立的新承诺，例如“正在帮你打开计算器。接下来我马上帮你播放音乐。”仍能触发补派发。明确的“我帮你打开”“我这就重新来”及原来的科学计算器漏调用修复继续覆盖。

原问答和三种进度变体已加入 [voice_task_progress.json](../../tests/fixtures/voice_task_progress.json) 的 `recorded_status_case`。用例 A 现在共七种输入/回复组合 × 两条收尾路径，检查原任务继续、没有额外任务排队、状态聊天正常落盘。只恢复这句实际回复的旧误判，两条收尾路径均因额外排入一个任务而失败；修复后通过。

开发宿主的源码监听在 18:47:02 自动重启 Engine，加载本次补丁；语音连接需要用户重新开启。历史失败记录保留，不改写为成功。本次没有再调用付费模型进行在线验证。

本次全量验证：pytest `348 passed, 12 skipped, 21 subtests passed`；Pyright 当前配置范围内 `0 errors, 0 warnings`；`git diff --check` 通过。自动化回归已完成，修复后的真实语音交互仍待复验。

## 2026-10-13 用户复验：问号被真实工具调用派发

20:07:33 用户只发“？”，Qwen 回复“正在处理，你先忙别的。”并实际调用 `run_task({})`。宿主启动任务 `677a19f4123c`，后台返回“没看懂你想问什么”的澄清，却标成 `completed` 且 `evidence_steps=[]`，最终触发 `missing_evidence`。这条路径不经过执行承诺补派发规则，之前的进度句补丁没有覆盖它。云端 PR #8 已合并，但这些后续本地修复尚未包含在 main。

处理分为两层：

- Application 接收真实或补派发的 `run_task` 时，排除纯标点、少量明确聊天和状态问答；返回 `not_delegated`，不创建、排队或取消任务。状态问答提示使用 `task_status`，其他内容由前台正常聊天或澄清。拒绝后仍保留原 Interaction 及工具结果回复映射，后续答复正常落盘且不关联 task ID。短确认“继续”“可以”等仍交给模型结合历史判断；混合了新操作的问答仍可派发。这是窄范围的排除规则，不是全面的意图分类器，未覆盖所有聊天句式。
- 后台增加 `needs_input` 结果，展示“待补充 / Needs input”，保留具体问题；与环境或登录等 `blocked` 障碍区分。若格式正确的 JSON 错将明确澄清标为 `completed`，只在没有完成声明且含明确询问时整理一次格式，不调用操作工具。修复必须保持原问题、空步骤和 `needs_input`，不能编造问题、证据或改成成功。真正声称已完成的无证据结果仍报错；操作后不引用观察的澄清仍被拒绝。

固定用例 [voice_task_clarification.json](../../tests/fixtures/voice_task_clarification.json) 保存本次原文、真实工具名及后台结果，事件外壳由离线替身构造。回归覆盖真实调用拒绝、原 Interaction 保留及回复落盘、后台任务不受影响、补派发拒绝不产生连接错误、澄清格式修复、无依据完成不能降级，以及操作后的证据要求。原六轮科学计算器串线用例继续通过。

本次全量验证：pytest `357 passed, 12 skipped, 40 subtests passed`；Pyright 当前配置范围内及新增排除规则/Runtime/结果校验文件均 `0 errors, 0 warnings`；面板 `pnpm run build` 与 `pnpm test`（11 项）通过；`git diff --check` 通过。Engine 已因源码监听自动重启加载，真实语音及在线模型复验仍待完成；改动未提交。

## 2026-10-13 决策模型每轮意见

用户要求每轮都取得决策模型意见。20:07 的原始 `context/turn/prepare` 记录显示 `jev.status=ambiguous`：旧 TurnPreparer 将不足三个字符或“继续、可以”等输入直接跳过，Qwen 只收到环境参考。旧 `inflight` 去重还会在上一轮决策未结束时跳过下一轮。两者均与每轮请求意见的要求不符。

现在启用决策参考时，每条正式输入都建立独立的决策任务，包括纯问号及短确认；之前未完成的决策不会阻止下一轮请求。子进程仍按请求 ID 串行处理，每轮只接收自己的结果，关闭时取消所有未完成任务。400 毫秒准备预算暂时保留：超时、失败或无效概率会显式写入当前参考，不伪造高低概率、不沿用上一轮结果。关闭参考或未配置服务时仍标记 disabled。这次不改变 JEV 的二分类题目，也不交付 REQ-06 的三分类与参与判断。

回归覆盖问号/短确认/正常请求逐轮概率、上一轮忙碌时新请求仍调用、晚到概率不污染下一轮、全部任务退出清理、异常与非法概率，以及真实 InteractionService 的文字与语音准备入口均传入当前概率。在线服务是否及时返回仍需真实模型验收。

本次全量验证：pytest `361 passed, 12 skipped, 57 subtests passed`；Pyright 当前配置范围及 TurnPreparer 文件均 `0 errors, 0 warnings`；`git diff --check` 通过。Engine 源码监听已自动重启加载；未调用在线模型，改动未提交或推送。

## 2026-10-13 20:31 重启与在线复验

通过应用正常退出及 `./启动桌宠.command` 重启，Electron 与 Engine 均重新创建；桌宠和对话小窗可见。针对本次问题重跑离线用例：`tests/test_turn_preparation.py tests/test_executor.py tests/test_voice_task_sequence.py`，`73 passed, 44 subtests passed`。

隔离 Product Session 进行五轮真实在线测试：生产 TurnPreparer、决策 Worker、Qwen AOQ、Interaction/Execution、Codex Runtime 与计算器操作均实际运行。关闭验收会话的长期记忆和麦克风，Qwen 输入为文字、音频不播放，未修改日常会话。报告保存在 `/var/folders/1j/drzjdpbs3b34gjr4h1gv413w0000gn/T/boxagent-live-query-20261013-r2n5opob/report.json`，临时执行脚本 `/tmp/boxagent-live-query-check-20261013.py`。

| 轮次 | 输入 | 实际 P(action) | 结果 |
|---|---|---|---|
| 1 | ？ | 0.11 | “嗯，我在听。”；无 task ID |
| 2 | 你好，我只想聊两句，不要操作电脑。 | 0.0 | 正常聊天；无 task ID |
| 3 | 打开计算器，10+10，确认20 | 0.96 | 任务 `79aa0441a071` completed；28.2 秒、8 步实际观察 |
| 4 | 第三轮执行期间问“你现在在干嘛呢？” | 0.06 | 正常说明进度；不替换、不取消、不排队新任务 |
| 5 | 科学计算器，二的五次方加根号42 | 0.96 | 任务 `c4c800b9e9a0` completed；36.6 秒、11 步实际观察；实际窗口核验表达式 `32+√(42)`、显示 `38.4807407` |

五轮参考都含有效概率；无 `missing_evidence` / `invalid_evidence`，两次结果关联各自原请求。它证明当前文字输入与真实任务执行路径本次通过，不代表原六轮音频故障或多人全双工已全面验收。两次任务采用动态工具观察路径，不能算 FIX-04 原生 MCP 补丁在线验收。通知已入 outbox、进入 delivering，报告采样后便关闭会话，未等完整播报回执；通知投递完成仍不能标为本次通过。

另一个实际限制：日常应用在 20:33 的“？”和“没事”两轮均正常聊天、无 task ID，但 `jev.status=timeout`（准备预算约 400 毫秒）。因此“每轮发起请求”已覆盖，“每轮 Qwen 开口前拿到有效概率”仍不保证。需单独调整等待与异常策略，不能用这次五轮通过掩盖日常超时。

## 2026-10-13 决策等待与补丁收敛

用户接受数百毫秒延迟，取消决策参考的 400 ms 截断。当前实现覆盖前述“每轮发起但来不及取得结果”的限制：可选记忆/屏幕在 400 ms 截止，决策独立等待（服务请求 4 s、宿主故障期限 5 s）；超时及取消结束对应请求，不复用旧结果。有效结果在 Qwen 回复前安装。输入为当前 Query 与最近八条正式消息、最多 2400 字符；按历史在前、当前 Query 在后的数据块组织。

删除三组语言启发式补丁：

- 中文聊天/进度固定词表改为本轮决策意见的保守接收保护。纯标点仅保留无操作目标的结构检查，仍运行决策。操作概率 ≤ 0.2 且聊天或进度概率 ≥ 0.8 才拒绝误调；不凭分数启动任务。短确认和模型分歧交 Qwen 判断。
- Qwen 传输层执行承诺正则改为领域层语义复核：联合原请求、近期对话、本轮回复，明确执行承诺概率 ≥ 0.8 才补派发一次。复核期间保留 Interaction，完成前再次核对归属；迟到实际调用仍去重。决策不可用或关闭时不凭句式自动派发。
- 删除把格式正确的 `completed` 按中文句式改为 `needs_input` 的修复。澄清须显式返回 `needs_input`。输出无法解析或遗漏观察编号时，工具为空的格式整理可读取本任务真实观察；补编号不得改 outcome/summary，虚构编号、空证据和失败观察仍拒绝。

同时移除历史 0.05/0.15 的高、中、低分档，仅传原概率。已有任务身份绑定、通知等待、去重、最终证据验证和 FIX-04 原生观察接入保留。新接收阈值与复核阈值是保守产品策略，未做用户分布校准；操作、聊天、进度是三道独立二选题，概率可能相互分歧，不等于 REQ-06 的静默与多人参与判断。

### 在线验证及失败记录

先试单道三选题时，五轮中三轮返回无效结果，状态询问也受到历史操作影响，未采用。改为三道独立二选题，并让当前 Query 在历史之后。中间批次五轮均有有效意见，但第一计算器任务出现 `missing_evidence`：8 步真实操作与最终观察已存在，后台仅输出自然语言；旧格式整理不知道真实观察，生成了空编号。这是结果整理遗漏依据，不是未启动任务，也没有用降低证据门槛修复。失败报告：`/var/folders/1j/drzjdpbs3b34gjr4h1gv413w0000gn/T/boxagent-live-query-20261013-q1qm4c93/report.json`。

最新五轮真实 Qwen AOQ / 决策 Worker / Codex 计算器批次七项检查均通过：问号、闲聊没有任务；操作中询问进度不替换、不取消、不排队；10+10 与 2⁵+√42 都 completed；每轮有效概率；无证据错误。报告：`/var/folders/1j/drzjdpbs3b34gjr4h1gv413w0000gn/T/boxagent-live-query-20261013-o3s1rkbv/report.json`。该批次在新增格式整理观察传递之前通过，不能算新增路径的直接验收。随后将真实失败的自然语言结果和已有第 8 步观察交给真实 DeepSeek 工具为空的整理回合，实际返回 completed、evidence_steps=[8]，宿主校验通过；未再次操作计算器。

独立在线承诺复核：原科学计算器漏派发句得到 0.90；“不要操作，只继续聊天”得到 0.24；“正在帮你打开计算器”的进度句得到 0.79，均按当前阈值正确处理。后者靠近阈值，不能宣称模型永远不会误判。超过 400 ms 的决策在在线批次（541.9 ms）及确定性测试（450 ms）均进入本轮。完整六轮音频、多人全双工与完整通知播报仍待验收，动态计算器操作也不代表 FIX-04 原生 MCP 的在线路径已验收。

最终验证：`UV_PYTHON=3.12.12 UV_CACHE_DIR=/tmp/uvcache uv run --with pytest python -m pytest -q`：368 passed、12 skipped、73 subtests passed；`uvx --from pyright==1.1.414 pyright`：0 errors。另对本轮主要修改文件（TurnPreparer、DecisionAdvice、InteractionService、IntentReference、Codex/Qwen Runtime、Harness Executor）使用已安装应用 Python 解析依赖执行 Pyright，0 errors；`git diff --check` 通过。Engine 源码监听已自动加载修改。未提交或推送。

## 2026-10-13 21:11 计算器元素编号误作证据

日常会话 `ses_fd8c10942c424fe0` 的任务 `99b81f32b249`，原请求“帮我打开计算器”。实际只有一次 `get_app_state(Calculator)`，返回计算器进程 pid 8009、窗口「计算器」、算式与结果等界面内容。后台最终回复却将界面元素索引 4（算式）、6（结果）、59（模式）误当作观察步骤，提交 `evidence_steps=[1,4,6,59]`；实际观察只有 `[1]`，因此触发 `invalid_evidence`。不是本轮未启动，也不是计算器启动失败。随后用户“为啥没法打开”的任务 `2704dc7cb5d4` 成功重新观察与置前，进一步确认应用正常。

上一轮补齐的结果整理仅处理缺失编号，没有处理错误编号，遗漏此场景。当前统一整理入口涵盖缺失和错误引用：只有存在本任务真实观察才允许一次工具为空的复核；明确区分观察步骤与界面元素索引，只纠正 `evidence_steps`，保持原终态 JSON 的 outcome/summary。若原回复为自然语言加末尾 JSON，使用与 Harness 相同的中立 Runtime 解析函数，不再用严格整段 JSON 解析。修复后仍校验当前编号和失败观察，不截取合法编号充数、不复用历史记录、不重复执行操作。工具返回与稳定指令也明确说明两类编号区别。

真实失败原文及观察已保存为 `tests/fixtures/calculator_evidence_indices.json`。回归覆盖原始错误先被拒绝、单次修复、原结论保留、工具不重放、无当前观察不能修复、修改结论/保留非法编号/失败观察不能通过。

验证：

- 完整 pytest：372 passed、12 skipped、77 subtests passed；Pyright 配置范围与本轮新增 Runtime 解析/修改结果处理文件均 0 errors；`git diff --check` 通过。
- 同一个真实隔离 Product Session，Qwen AOQ → 决策 Worker → Codex → 计算器连续三任务：打开计算器 `b74099954503`（3 步，10.568 s）、计算 10+10 `fb66dfcd38d9`（8 步，24.770 s）、再次打开 `b03298e724c4`（1 步，7.338 s），都 completed、无证据错误，三次未进入整理回合。报告：`/var/folders/1j/drzjdpbs3b34gjr4h1gv413w0000gn/T/boxagent-calculator-evidence-un85bkt3/report.json`。
- 真实失败原文加真实观察，交给真实 DeepSeek 工具为空的复核，返回 `[1]`，原 summary/outcome 逐字保留，宿主验证 completed；没有操作计算器。报告：`/var/folders/1j/drzjdpbs3b34gjr4h1gv413w0000gn/T/boxagent-recorded-evidence-qf_8kxxu/report.json`。

日常 Engine 已通过源码监听自动重启加载。改动未提交或推送；上述文字入口和实际桌面操作验证不代表完整音频或多人全双工已经验收。

## 2026-10-13 任务摘要泄露内部验收信息

日常任务 `8c127a0839d7` 的 `summary` 原文包含“pid 8009”和“步骤 1、2”，虽然 `evidence_steps=[1,2]` 已单独保存，摘要仍沿用验收报告的表达。该字段直接用于 Product 消息、面板及通知，所以问题出在后台生成的摘要，并非界面额外拼接了这些字段。

稳定指令与结果 Schema 现在明确区分用户答复和内部依据：`summary` 用自然简短的话说明当前请求的实际结果或具体澄清问题；默认不带 PID、bundle ID、步骤引用、操作流水账，也不补充无关的旧算式、模式或下一步建议。用户主动要求技术细节时仍可提供。证据独立保存在观察记录和 `evidence_steps`，完成校验没有放宽。自然语言结果的格式整理与 Qwen 通知采用相同表达要求；已有终态 JSON 的证据修复仍逐字保留原 outcome/summary。没有新增文字过滤规则或每任务额外的润色模型调用，也未改写历史记录。

第一批真实三任务已去除 PID/步骤引用，但再次打开仍夹带旧算式与清理建议；继续收紧当前目标约束后，第二批同一隔离 Product Session 经真实 Qwen AOQ、决策模型、Codex 和计算器执行：

| 请求 | 任务 | 实际摘要 |
|---|---|---|
| 帮我打开计算器 | `eb99fc486562` | 计算器已经打开了，可以开始算了。 |
| 请在计算器算10加10，并确认结果。 | `ecd11876da99` | 算好了，10+10=20，计算器上已经显示结果啦。 |
| 帮我打开计算器 | `ddd923491025` | 计算器已经打开了，可以开始算了。 |

三任务全部 succeeded，均有本任务真实证据，没有证据错误。报告：`/var/folders/1j/drzjdpbs3b34gjr4h1gv413w0000gn/T/boxagent-calculator-evidence-6ftad7bk/report.json`。该验收验证生成摘要与真实文字任务路径；音频关闭，未验收最终语音播报，不代表所有模型回复均能保证符合风格。

全量 pytest：372 passed、12 skipped、77 subtests passed；Pyright 配置范围及本轮四个修改文件均 0 errors；`git diff --check` 通过。源码监听自动加载，改动未提交或推送。

## 2026-10-14 提交前验证

本次提交汇总前述逐轮决策等待、领域语义补派发、原生观察接入、证据引用整理、用户摘要表达及固定回放用例；REQ-06 仍为设计文档。补齐 App Server 管道、可选上下文/Skill 服务和观察脱敏返回值的边界检查。历史各阶段“未提交”描述仅对应当时状态。

提交前重新验证：pytest 372 passed、12 skipped、77 subtests passed；规定 Pyright 范围及额外覆盖的十二个修改模块均 0 errors；面板 11 项测试及 TypeScript/Vite 构建通过；`git diff --check` 通过。完整音频、多人参与与原生路径在线验收仍保留上述限制，不因提交而视为完成。

## 2026-10-15 固定回归用例：明确操作被首轮决策误拦截

原始输入“关闭计算器”的记录中，决策概率为 `p_action=0.12`、`p_status=0.37`、`p_conversation=0.96`。Qwen 已回复“这就去把计算器关了。”并调用 `run_task`，宿主因聊天排除意见返回 `not_delegated`，没有创建任务；小窗因此保持空闲。伙伴后续描述的“执行失败”实际发生在派发之前。

当前只在明确 `run_task` 调用与首轮排除意见冲突时，使用已有语义复核检查本轮原始输入、近期正式对话和对应回复。复核通过且原交互仍有效才派发；无对应回复、复核失败或交互已被打断时保留拒绝。没有按计算器关键词绕过决策，也不会因口头承诺直接显示运行中。

离线回归入口为 `tests/test_turn_preparation.py::TurnPreparationTests::test_explicit_tool_call_repairs_recorded_close_calculator_false_negative`：通过生产 Qwen/Interaction/Execution 链路重放原始低分与工具调用，检查执行目标仍为“关闭计算器”且真实任务进入 running；决策复核与执行器为替身。全量 pytest 为 377 passed、12 skipped、77 subtests passed，规定 Pyright 范围 0 errors。此回归不代表真实决策模型或完整语音操作的在线验收已完成。
