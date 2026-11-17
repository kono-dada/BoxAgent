# 首次引导视觉复核

2026-11-17，分支 `codex/onboarding-packaging`。本轮围绕 MiraJelly release 的引导结构重新实现，随后按用户反馈减少文案、提高对比度，并将 DeepSeek 改为必填。结论是同一类紧凑居中引导结构，保留 BoxAgent 的凭据流程；不声明像素一致或全面通过 Apple HIG。

## 参考与验证方式

MiraJelly `/Users/bytedance/MiraJelly` 保持 `release`，HEAD `6876199761af930d238c08abb18f2bb98e30358e`。读取了 `OnboardingShell.tsx`、`onboarding.css`、`SetupFlowStep.tsx`、`UnifiedSetupStep.tsx`、`ReadyStep.tsx` 和原生窗口呈现代码。使用临时隔离项目直接编译其 `OnboardingShell`、`ReadyStep`、`UnifiedSetupProgress`，传入模拟就绪/下载状态，在同版本 Electron 的 500×570 HUD 窗口中截图。该验证针对 UI 组件，不是运行 MiraJelly 全部首次安装流程。

BoxAgent 使用真实 React 页面与生产 setup IPC，Provider、权限和 Engine 边界为测试 fixture。截图、尺寸记录和并排图保存在 `.runtime/onboarding-smoke/`；参考编译文件位于 `/tmp/boxagent-mira-reference/`，不进入 BoxAgent 源码或安装包。

| 项目 | MiraJelly release | BoxAgent 本轮 |
| --- | --- | --- |
| 标准窗口 | 500×570 内容尺寸，macOS HUD vibrancy | 同样的内容尺寸与原生材质 |
| 导航 | 顶部 48 px 区域，6 px 圆点，活动胶囊 20 px | 相同视觉形状，扩展可点击区域；步骤使用 BoxAgent 的五步流程 |
| 标题 | 26 px / 34 px，中文使用常规字面 | 同样尺寸；本机 PingFang 常规字面，英文为系统字体 |
| 底部主按钮 | 160×40，中心 x=250，y=502 | 普通页面同样的位置；大幅缩放时底部操作换行 |
| Ready | 对勾、一切就绪、折叠设置详情 | 对勾、准备好了、折叠能力状态；删除重复段落与底部日志链接 |
| 准备页 | 当前任务、进度与反馈 | 保留当前进度、取消、简短空间提示，组件清单折叠 |
| 业务差异 | 本地准备与账户连接流程 | DashScope Key、该 Key 对应的业务空间 ID、DeepSeek Key 全部必填，两组服务鉴权通过后继续 |

## 逐页人工复核

逐张查看真实 Electron 截图，复核五页中文版、关键英文页面、Key 的三个焦点状态及组件下载状态。以下是本次实测结果，不代替有效 Key、真实 TCC 授权或新 Mac 验收。

| 页面 | 人工观察与修正 |
| --- | --- |
| 欢迎 | 标题、图标与短介绍居中；移除额外功能卡片后，主要动作与留白清楚 |
| 模型服务 | 原外扩焦点框被滚动容器裁切；改为输入框内侧的完整高亮。缩短间距，保证首次填写时密钥保护说明完整可见；三项必填。按用户澄清，删除地域提示，填写 Key 对应的业务空间 ID |
| 本地组件 | 常规状态与下载状态都显示当前进度；下载取消可见，详细清单折叠 |
| 权限 | 原重复组件说明导致英文刷新按钮部分裁切；保留简短的稍后授权提示，将组件要求留在详情中；标准尺寸下刷新与下一步完整可见 |
| Ready | 与 MiraJelly release 的实际 ReadyStep 截图对照，保留对勾、简短标题、折叠状态与主按钮；就绪绿色不承担唯一状态提示 |

键盘焦点、密钥保护说明和权限刷新按钮有对应截图或可见性检查。长详情与 200% 缩放仍使用滚动区域；底部主按钮保持可见。

## 配色与信息层级

MiraJelly 原引导为 `rgba(10,11,15,.72)` 背景，主文字 `#f6f6f8`、次文字 `#b4b6be`、说明 `#92959f`，操作强调 `#5f94ff`。前期将按钮也改成蓝灰色后，操作辨识度下降；本轮恢复蓝色操作强调，并根据用户“文字和背景太接近”的反馈加强亮度和背景遮罩。

| 用途 | 最终值 | 对比度边界 |
| --- | --- | --- |
| 窗口遮罩 | `rgba(10,11,15,.9)` | 降低明亮桌面对文本的影响；仍使用原生 HUD |
| 主文字 | `#f6f6f8` | 标题与主要字段 |
| 正文 / 说明 | `#c4c7cf` / `#acb2bf` | 对保守估算的亮背景 `#232427` 为 9.18 / 7.30 : 1 |
| 主按钮 | `#3e6fcb`，白字；hover `#3662b7` | 白字分别为 4.84 / 5.85 : 1 |
| 禁用主按钮 | `#33425c`，文字 `#c0c8d7` | 6.02 : 1；不再给整个按钮叠加透明度 |
| 就绪标记 | `#8fcbae`，浅色低透明度圆底 | 按用户最终反馈恢复柔和绿灰色；同时有对勾和文字 |

比例按 sRGB 相对亮度计算，只代表列出的前景/背景组合，不代替各种显示器、真实壁纸、VoiceOver 或完整辅助功能验收。主体文字 13 px，标题 26 px；细节文字保持 10–11 px。英文保留系统字体，没有携带 MiraJelly 的品牌字体。

## Apple Design skill 的取舍

安装并读取了 [apple-design](https://github.com/dickwu/apple-design-skill/tree/91e15664896f6f27e75dcf1414d1226cb95fa167) 的 SKILL 和相关 HIG 摘录。它最有用的是信息层级与减法，不是套用另一种装饰。

- [Onboarding › Best practices](https://developer.apple.com/design/human-interface-guidelines/onboarding)：要求 prerequisite onboarding “doesn’t require people to memorize a lot of information”。因此删除欢迎页的三块功能介绍，Ready 只留确认；DS 和本地模型按本产品明确的先决条件保留。
- [Layout › Visual hierarchy](https://developer.apple.com/design/human-interface-guidelines/layout)： “Use progressive disclosure to make layouts cleaner”。组件、获取 Key 的说明和桌面执行工具放到详情，必填字段直接显示。
- [Color › Best practices](https://developer.apple.com/design/human-interface-guidelines/color)： “Avoid using the same color to mean different things”。蓝色集中表示主操作、进度和焦点，就绪另有对勾；没有以颜色代替状态文字。
- [Typography › Ensuring legibility](https://developer.apple.com/design/human-interface-guidelines/typography)： “In general, avoid light font weights”。保留系统字体和常规中文字面，提高正文与说明对比度。
- [Text fields › Best practices](https://developer.apple.com/design/human-interface-guidelines/text-fields)： “Use secure text fields to hide private data” 和可预测的 Tab 顺序。两个 Key 仍为密码输入，保存失败保留内容，成功后清空输入；回车可提交。
- skill 的 Liquid Glass 摘录强调功能层与内容层分离。因此保留原生窗口 HUD，移除内容卡片的额外 backdrop blur，不将 CSS 模糊冒充原生 Liquid Glass。

取舍：为匹配参考，此引导保持固定标准尺寸和深色呈现；通过滚动、200% 缩放重排、减少透明度与增加对比度的替代样式保证已测路径可用。这不等于完整实现系统字体偏好、所有窗口尺寸或 HIG 全面适配。

## 已验证的边界

67 个 Node 测试通过，包括缺失/无效 DeepSeek 无法完成、旧已完成数据缺少 DS 返回配置且保留凭据。真实 Electron fixture 覆盖五步中英文、200% 缩放、始终可见的主按钮、Key 必填、回车提交、Keychain/鉴权失败、切换语言保留输入、下载取消、权限状态、减少动态效果/透明度及高对比度。成品移至 checkout 外的只读、含空格路径，仅系统 PATH 的 UI 检查另行记录于 [安装包验证](macos-validation-2026-11-17.md)。

DS 验证使用服务鉴权接口，不把它等同于实际桌面任务完成。官方执行组件与真实 TCC、云推理验收仍遵循安装包验证记录中的边界。
