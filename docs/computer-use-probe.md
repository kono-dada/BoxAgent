# Computer Use 独立接入预实验

> 最新进展：已通过 **Python → 官方 Codex 0.153.0 App Server → Computer Use → 真实计算器**，包括用户从独立 Terminal 启动的读取验证。已定位 0.154.0 的握手兼容性问题。桌宠接入方案、可导入的 Python 接口和最新边界见 [Python 接入说明](python-codex-computer-use.md)。以下保留早期实验记录，其中“Python 未通过”指原始执行器直连或当时的版本组合。

验证日期：2026-09-14。本机 Computer Use 版本：1000968。

> 结论修正：此前的 Node 成功发生在 Codex 启动的进程树内，不能推出任意第三方 App 均可独立调用。后续发现执行器具有父进程和可信祖先认证机制。Python 的 MCP 协议实现已完成，但操作能力仍未通过；详见下方身份诊断。

## Python 入口

`scripts/computer_use.py` 是可独立运行、也可导入的 Python 协议实现。仅使用标准库，由 uv 管理 Python 环境，不需要 API Key、Node 或 Codex 模型。仍需本机安装 Computer Use 执行器并具备相应系统权限。

**当前状态：握手和工具列表已通过，真实界面读取未通过，因此 Python 版尚不能作为可用执行器。** 本机实测中，默认 `asyncio.create_subprocess_exec` 启动方式多次在首次 `get_app_state` 超时；对照的 Node 探针成功。进程启动对照中使用 `close_fds=False` 后，执行器明确返回 `Computer Use server error -10000: Sender process is not authenticated`。该启动参数未保留在最终代码中，也未尝试绕过认证。尚不能确定默认启动方式超时与该认证错误是否同一原因。

```sh
# 只握手并列出工具，不操作 App。
uv run --script scripts/computer_use.py

# 计算器演示入口；当前机器上该 Python 路径未通过，预计可能超时。
uv run --script scripts/computer_use.py --demo --allow-calculator-once
```

可复用的接口是 `ComputerUse` 异步上下文管理器，提供 `list_tools()`、`get_app_state(app)`、`call(tool, **arguments)` 和 `text(result)`。宿主通过异步 `approve(params)` 回调承接 App 授权；默认拒绝。回调返回 `True` 仅适用于不需要额外内容的空表单，不能代替复杂表单或身份验证。示例的许可仅精确匹配计算器。

```python
from scripts.computer_use import ComputerUse

async def read_calculator(approve):
    # approve 应由你的 App 展示授权问题并返回用户的选择。
    async with ComputerUse(approve=approve) as computer:
        state = await computer.get_app_state("com.apple.calculator")
        return computer.text(state)
```

如果演示能完成，输出将保存在 `.runtime/private/artifacts/computer-use/python/`，含界面文本、截图和核验结果；本次没有生成 Python 成功截图或通过记录。超时或取消会发送 MCP 取消通知，但仍不能保证撤销已发出的动作。当前已通过的 Node 路径见下文。

### Python 与 Node 的身份诊断

语言本身不构成 macOS 权限，但运行时可执行文件的签名、父进程和负责进程可以被检查。此次检查的是同一个 OpenAI 签名的 `SkyComputerUseClient`，没有修改它的签名、配置或权限。

实测对照：

| 条件 | 结果 |
| --- | --- |
| 当前 Codex 启动的 Node 探针 | 重新实测取得计算器界面文字和截图 |
| uv Python，普通管道 | 握手和列表成功；操作曾返回认证错误，其他运行超时 |
| uv Python，本地 socketpair | 握手和列表成功；操作返回 `Sender process is not authenticated` |
| Apple 签名的系统 Python 3.9 | 握手和列表成功；操作在 8 秒对照期限内未返回 |
| Python 显式 posix_spawn，重置子进程信号 | 握手和列表成功；操作在 8 秒对照期限内未返回 |
| Python 对照 libuv 的 Darwin spawn 属性及 socketpair | 握手和列表成功；操作在 8 秒对照期限内未返回 |
| 工具列表后等待 0.5 秒 | 未解决读取超时 |

不能仅凭超时断言所有失败都是同一个认证原因；这些对照没有得到 Python 成功结果。签名与传输类型都不足以单独解释差异。

只读检查本机服务二进制发现：

- `ComputerUse.ComputerUseIPCSenderAuthorization` 类型，以及 `parentIdentity`、`responsibleIdentity` 属性。
- `allowedRelayRequirement`、`parentProcessIsCodex`。
- `untrusted_parent`、`relay_without_trusted_ancestor` 错误类别。
- 父进程团队 ID、签名 ID、可执行路径和可信祖先相关诊断字段。
- Node 的团队 ID `HX7739G8FX` 以及 `node`、`node_repl` 标识；本机 Node 的真实签名与该团队 ID 相符。

以上证据明确表明存在宿主调用链认证。结合 Node 成功而 Python 失败，支持“Node 路径符合受支持中继条件，Python 路径不符合”的判断；但本次未取得完整源代码，不能把字符串提取当成完整授权规则的证明。未通过改名、换签名、移除检查或伪造祖先来试图让 Python 被接受。

两层授权必须区分：

1. **宿主认证**：执行器是否接受调用进程的来源；当前 Python 失败发生在这里或在进入该阶段时超时。
2. **App 授权**：用户是否允许本次使用计算器；对应 MCP `elicitation/create`，代码已正确承接。

Python 类现在单独抛出 `ComputerUseAuthenticationError`，不会把认证失败报告为计算器授权被拒绝，也不会悄悄切换另一种调用身份。当前尚未找到允许第三方 Python 宿主注册的公开入口，纯 Python 直连不能标为完成。

复现脚本为 `scripts/diagnose_computer_use_transport.py`。这是启动方式对照工具，包含实验性的私有 Popen 覆盖与 Darwin ctypes 调用，不是正式客户端依赖。静态符号证据保存在 `.runtime/private/artifacts/computer-use/python-diagnosis/authentication-symbols.txt`；诊断 JSONL 只记录计算器结果摘要与元数据，不保存截图数据。

参考：Apple 区分 [父进程与负责进程的审计令牌](https://developer.apple.com/documentation/endpointsecurity/es_process_t)；[libuv 的进程启动实现](https://raw.githubusercontent.com/libuv/libuv/v1.51.0/src/unix/process.c)显示其在 macOS 上的 socketpair 和 posix_spawn 设置。这两份资料解释系统概念及对照方法，不构成 OpenAI 对第三方接入的支持声明。

## 已验证

第一组实验使用独立 Node 进程，通过标准输入输出发送 MCP JSON-RPC。子进程仅继承 HOME、PATH、TMPDIR、LANG 和可选 CODEX_HOME，没有注入当前 Codex 会话的令牌或运行时变量。这组实验未调用模型 API；后续 App Server 实验使用本机已有 Codex 登录和模型配置。

1. `initialize` 和 `tools/list` 成功，首次从启动到列出 10 个工具约 174 毫秒。
2. `get_app_state` 指定 `com.apple.calculator` 后，收到 `elicitation/create` 授权回调。问题为 `Allow ChatGPT to use Calculator?`，请求的表单为空对象。
3. 拒绝回调时，执行器返回授权拒绝错误。第一次探针未实现回调时返回方法不存在错误；该错误来自探针，不代表执行器禁止第三方调用。
4. 用户批准后，探针对精确匹配的计算器授权返回 `accept` 和空 `content`，没有请求持久化许可。成功取得真实计算器可访问性树和 JPEG 图片数据。启动到返回约 1854 毫秒；这个数字不是模型执行延迟。
5. 读取到计算器原有表达式 `768÷32` 和结果 `24`。探针没有输入这个表达式，也没有执行点击或键盘操作。

## 复现

使用系统已有 Node，不需要安装依赖。命令从项目根目录运行。

```sh
node scripts/probe-computer-use.mjs
node scripts/probe-computer-use.mjs --calculator-state
```

第二条命令会记录授权请求并拒绝它。只有用户明确同意本次访问计算器时，使用：

```sh
node scripts/probe-computer-use.mjs --calculator-state --allow-calculator-once
```

在 Codex 沙箱内运行时，启动本地 App 内的执行器需要通过沙箱外执行审批。探针 20 秒超时，退出时终止自己启动的 MCP 子进程。图片仅记录格式与编码长度，不写入日志。

## 操作闭环与中止实测

- 独立 MCP 客户端从最新界面找到清除按钮，点击后输入 `137+248`，发送回车，重新读取到 `385`。返回截图也经过人工式视觉核验。整轮约 2748 毫秒，包含握手、四次界面读取和操作，不含模型推理。
- 另一次实验清除计算器后发送 `type_text("8642")`，随即发送针对该请求 ID 的 `notifications/cancelled`。请求仍正常返回，界面显示 `8,642`，两秒后再次读取仍保持此状态。此次通知没有阻止已发送的输入，不能据此承诺在途动作可取消，也不能推广为所有操作均不可取消。
- 取消实验没有发送后续回车。这是调用方停止派发的行为，不代表执行器支持撤销动作。
- 约 90 秒桌面观测共 1674 个样本，目标采样间隔 50 毫秒。前台 App 一直是 `com.openai.codex`，剪贴板变更计数未变。全时段鼠标有移动；按运行日志创建时间估算的算术和取消实验窗口内，分别有 52 和 70 个样本，鼠标位置均未变，前台仍是 Codex，剪贴板计数未变。采样不能排除采样间隔内的瞬时变化。
- 尚未收到用户连续打字体验反馈，因此不宣称验证了打字并发或所有 App 的后台兼容性。

证据在 `.runtime/private/artifacts/computer-use/`：`arithmetic/result.jpg`、两组实验的界面文本与 `events.jsonl`、`desktop-observation.jsonl` 和 `desktop-summary.json`。截图独立保存；日志中的图片字段仅保存格式与长度。

用户授权操作计算器后，可运行：

```sh
node scripts/verify-computer-use.mjs
node scripts/verify-computer-use.mjs --cancel
```

这两个命令会改变计算器当前输入。取消实验结束时当前输入为 `8642`。

## 自然语言派活实测

使用独立进程启动 `codex app-server --stdio`，继承本机 Codex 登录和配置，模型为本机默认的 `gpt-6-astra`。没有修改全局配置。

直接配置 MCP 的路径尚未通过：首次线程级配置没有让模型取得工具；改为进程启动参数后，`mcpServerStatus/list` 能列出四个工具，但模型仍未调用。进一步通过 `mcpServer/tool/call` 读取计算器时，收到明确错误：`MCP startup failed: handshaking with MCP server failed`，内部原因是 `The data couldn’t be read because it isn’t in the correct format.`。工具库存可见不等于线程执行时的客户端可用。尚未确定具体哪个握手字段不兼容。

已通过的转接方式：

```text
独立 Node 程序 → Codex App Server → 模型的 calculator_ui 动态工具
                      ↓ item/tool/call
独立 Node 程序 → 独立 MCP 客户端 → 本机 Computer Use → 计算器
                      ↑ 界面文字与执行结果
```

动态工具仅开放读取、点击、输入和按键，宿主固定目标为计算器；承接用户已经授权的计算器许可，不接受其他 App 授权请求。MCP 客户端使用此前已实测通过的 `2024-11-05` 初始化格式。此实验只向模型返回可访问性文字，截图不是这次模型决策的输入。

给模型的任务是实际操作计算器计算 `219+436`。日志证实模型发起五次调用：读取、点击清除、读取、输入 `219+436=`、再次读取。最后一次界面数据包含 `655`，模型报告 `219 + 436 = 655`。总耗时约 29.19 秒，包括 App Server 启动和工具准备；首个实际工具请求约在第 17.92 秒。成功不依赖模型心算。

外部程序收到模型进度消息、动态工具请求、结果与 `turn/completed`。本次未测 App Server 的 `turn/interrupt` 在真实动作期间的表现，不能将 MCP 取消测试等同于整个 Agent 的中止测试。

复现通过的路径（使用本机 Codex 登录额度）：

```sh
node scripts/verify-computer-use-agent.mjs --bridge
```

相关文件：`scripts/computer-use-mcp-client.mjs`、`scripts/verify-computer-use-agent.mjs`；证据为 `.runtime/private/artifacts/computer-use/agent-bridge/events.jsonl`。另保留 `agent-first-attempt.log` 和 `agent-native-handshake-failure.log` 作为失败边界记录。不加 `--bridge` 是尚未通过的直接 MCP 配置路径。

## 结论边界

已证明：当前 Codex 启动链中的独立 Node 进程能够通过 MCP 调用本机执行器，承接授权后读取界面、点击、输入并完成固定算式。这组调用不经过模型或 App Server，但未脱离 Codex 进程树验证。

已证明：借助一个由自己程序承接的动态工具转接层，Codex App Server 可以驱动真实 Computer Use 操作并回传结果。这是已安装本机环境中的技术可行性证据，不是独立 SDK、再分发许可或官方稳定支持声明。

未证明：连续打字并发、音乐 App 兼容性、可靠取消在途动作、退出 Codex 桌面客户端后仍可用、全新机器可部署、对第三方提供稳定支持或再分发许可。

## 后续最小实验

1. 音乐 App 的真实播放与状态核验。
2. 用户连续打字时重复操作，记录主观干扰和桌面元数据。
3. 若产品要求强中止，进一步研究执行器取消契约；当前只能稳妥地串行派发小动作，在动作之间检查取消，并回报最后观察状态。

前两轮无需桌宠、事件总线、长期记忆或完整 Agent 框架。
