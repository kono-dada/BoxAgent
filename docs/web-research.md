# 游戏攻略与联网资料

2026-11-17，首个验收游戏为《鸣潮》，用于 BoxAgent 宣传片。开发分支为 `codex/game-web-search`，基于已完成的 onboarding 分支 `f15e53a`。

## 接入结论

DeepSeek 已开放独立 Search、Fetch API，复用 `DEEPSEEK_API_KEY`，无需额外搜索供应商或第二个 Key。开源的 [chat-demo](https://github.com/deepseek-ai/chat-demo) 是演示客户端；搜索服务通过云 API 提供，并不意味着开源了搜索引擎供本地部署。

用户提供的知乎文章在本次读取时返回 403，未将文章正文作为已验证信息。接口、能力和价格以以下官方文档为准：

- [构建 Chatbot](https://api-docs.deepseek.com/zh-cn/guides/build_a_chatbot)
- [网页搜索与抓取](https://api-docs.deepseek.com/zh-cn/guides/search)，[Search Schema](https://api-docs.deepseek.com/zh-cn/api/search)，[Fetch Schema](https://api-docs.deepseek.com/zh-cn/api/fetch)
- [模型内置联网工具](https://api-docs.deepseek.com/zh-cn/guides/web_tools)
- [Files API](https://api-docs.deepseek.com/zh-cn/guides/files_api)，[价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
- [Codex 集成与 Alpha Search](https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/codex)

本轮使用独立只读 API，保留 Qwen 的实时文字/语音前台。模型自行决定调用 `search_web` 或 `read_webpage`，应用校验参数、执行请求，再将外部资料返回当前对话。查攻略不依赖 Computer Use，不需要先启动浏览器或后台桌面任务。

```mermaid
flowchart LR
  A[玩家用文字或语音提问] --> B[Qwen 对话]
  B --> C[只读搜索与网页阅读工具]
  C --> D[DeepSeek Search / Fetch]
  D --> E[标题、来源、日期、摘要或正文]
  E --> B
  B --> F[简短建议与可点击来源]
```

## 首版已经实现

| 项目 | 行为 |
| --- | --- |
| 搜索 | `POST /v1/tools/search`，关键词 1–128 字，最多 5 条；返回标题、URL、摘要、站点及可用的发布时间 |
| 阅读链接 | `POST /v1/tools/fetch`，读取用户提供或搜索返回的公开 HTTP(S) 链接；正文最多回传 12,000 字，并明确标记截断 |
| 游戏判断 | 使用已确认的游戏、Boss/角色/任务、平台与版本；“这个 Boss”无法确认时先澄清，不猜关卡 |
| 来源判断 | 提示模型判断标题/正文是否回答问题，关键打法尽量核对不同站点，版本更新优先查看官方公告；同站多个页面不是独立证据 |
| 失败处理 | 无结果、鉴权、余额不足、限速、网页不可读、协议错误分别反馈；模型不得把失败说成已查到 |
| 请求边界 | 每个有 Interaction ID 的对话回合共最多 4 次 Search/Fetch；单次超时 20 秒，不自动重试付费请求 |
| 配置 | 默认启用并复用 DeepSeek Key；`BOXAGENT_WEB_SEARCH_ENABLED=0` 关闭。第三方模型网关必须同时支持独立工具接口 |

返回值携带检索时间和外部资料标记。发布时间、检索时间都不等于攻略适用版本；来源未知或冲突时需明确说明。没有自动把攻略写进用户长期记忆，也没有新增上传截图或文件的路径。

依赖保持 `bootstrap -> application/domain <- infrastructure`。WebProvider 协议和证据记录位于 domain，DeepSeek HTTP 适配器位于 infrastructure，生产依赖在 bootstrap 构造。没有安装第三方搜索 SDK，也没有复制 chat-demo 的实现。

当前前台已经接工具；后台 Codex 执行器的原生搜索没有开启。仓库的 Computer Use 基线为 Codex 0.153，官方 Alpha Search 需要 0.164+，需单独验证版本与 SDK 兼容后升级。DeepSeek 的模型内置联网工具还存在与 `custom` / `namespace` / `tool_search` 等工具的组合限制，不宜直接塞进现有 Code Mode 请求。

## 《鸣潮》如何验收

宣传片先选择固定 Boss 或任务、明确游戏版本与平台，再录制下面的场景：

1. “帮我查一下《鸣潮》周本无冠者的弹刀/闪避打法。”应真实检索，给出两三条当前情境能用的建议和来源；区分周本、野外、全息和梦魇形态。
2. “这套配队适合我当前版本吗？”应依据已知角色/版本核对；缺少关键信息时先询问，不自动套用最新数值。
3. 发送库洛攻略或 Wiki 链接，要求解释机制。应阅读原文；网页无法访问时说明，并可换另一条来源。
4. “最新版本这角色改了吗？”应查看官方公告或明确的版本说明，不能把搜索页面日期当成游戏补丁版本。
5. 网络失败、余额不足和无结果时，应明确未查到；不阻塞普通聊天，不伪造来源。

真实 Search + Fetch 测试已使用公开的《鸣潮》问题和现有 Key，没有发送用户聊天历史、截图或现场音频：

| 测试 | 实测与限制 |
| --- | --- |
| 普通关键词 | 搜索和读取共约 7.15 秒；前三条均来自九游社区，发表于 2025–2026 年。证明 API 通路，不证明最新版本适配或攻略质量 |
| 指定攻略站 | 共约 5.31 秒；出现 2022 年预告、2024 年其他模式攻略和 Wiki。说明站点筛选仍可能产生不相关结果，不能只看排名 |
| 排除重复站点并换来源 | 两条 NGA 页面无法读取；随后库洛 Wiki 的“无冠者（敌人）”正文读取成功，共约 7.33 秒。正文包含姿态、预警与声骸对策技说明；该页更新于 2026-03-14，不能据此声称适用于未确认的最新补丁 |
| 真实 Qwen 前台工具回合 | 关闭麦克风与播放，使用生产 Qwen WebSocket 适配器和应用工具分发，模型主动调用 Search、接收资料并带真实来源作答，约 9.99 秒，没有调用桌面操作。它只使用了搜索摘要，未进一步 Fetch 或核对多来源；不将此认定为攻略事实准确性、现场音频或宣传片完整验收 |

原始证据保存在 `.runtime/web-research/`，不提交 Git。以上时间来自本机少量请求，不代表稳定延迟或 p95。来源核对目前由模型提示与返回的证据支持，还不是已验证的自动事实校验器。

本地验证：全量 Python 测试 466 passed、13 skipped、99 subtests passed；指定版本 Pyright 为 0 errors，仅有原有 PyAudio 源码解析 warning。新增 Web 模块和实测脚本单独类型检查均为 0 errors、0 warnings；`git diff --check` 通过。测试覆盖参数、空结果、HTTP 错误、协议异常、回合预算、取消与过期回合、正文截断及不启动桌面任务的应用分发。

可复现命令（最多一次搜索、三次换来源读取，按实际成功请求计费）：

```bash
uv run --python 3.12 python scripts/experiments/web_research_smoke.py --live \
  --query '鸣潮 无冠者 打法 攻略 -site:9game.cn lang:zh'
```

脚本默认读取本项目的 Key；也可通过 `--env-file /path/to/.env.local` 指定文件，只读取 DeepSeek Key 与 API 地址。不会输出或保存 Key。

官方当日定价：Search 与 Fetch 分别为每 1,000 次成功请求 5 元。一搜一读约 0.01 元；一回合最多四次约 0.02 元。现有对话模型调用仍按自身价格计费；未来原生深度搜索还会增加模型 token 费用。

## 后续能力顺序

| 优先级 | 能力与场景 | 接入条件 |
| --- | --- | --- |
| P0 | 搜索＋链接阅读：Boss/任务攻略、配队、更新公告；也可复用到软件文档和产品资料 | 本轮基础接入；用明确版本的鸣潮台词逐条验证，再录制 |
| P1 | 来源卡片与版本提示、手动搜索开关 | 在聊天中展示可点击的实际来源，避免语音报长 URL；来源信息从工具结果绑定到当前 Interaction，不能由界面猜测 |
| P1 | 文件解析：玩家主动提供 PDF 攻略、表格或手册 | 增加显式附件上传、大小限制、云端文件有效期与删除，避免永久保留默认值；不自动上传私有文件 |
| P1/P2 | OCR / 图像搜索：玩家主动分享任务名、装备截图 | 先复用现有本地视觉；需要更精确文字或搜图时再接云 API。独立 Search 当前不支持图片搜索，需另一条模型内置工具路径 |
| P2 | 深度研究：多轮配队比较、长任务链查证 | 独立的 DeepSeek 研究请求，保留原始内容块/引用、取消、用量与时间上限；不替换实时聊天链路 |
| P2 | 后台 Agent 搜索 | 先升级并验收 Codex Alpha Search 或加入共享 WebProvider 网关，再验证 Computer Use、Skill 与观察证据不会冲突 |
| 后续 | ASR/TTS | 已有 Qwen AOQ 全双工，不急于替换；只有声音、质量或成本有明确收益时再做可选语音适配 |

首版验收要求是“真实搜索、可追溯来源、版本不乱说、失败不冒充成功”。基础工具接通后，仍需用选定宣传片台词检查实际前台回复和现场游戏体验；不能把测试 fixture、单次网页读取或提示词视为完整验收。
