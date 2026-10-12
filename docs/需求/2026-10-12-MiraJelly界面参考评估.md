# MiraJelly 本地界面参考评估

2026-10-12：用户明确要求管理面板也达到精致的产品界面标准。参考仓库位于 `/Users/bytedance/MiraJelly`，当前内部产品名为 NeraJelly。本次读取源码、验收记录并查看仓库已有截图，没有启动或修改参考应用。

## 参考里值得采用的做法

| 方面 | 已核实的参考做法 | BoxAgent 当前差距与改造方向 |
|---|---|---|
| 字体与层级 | 正文、说明、页面标题、导航分别有语义字号；常规正文 14/20 px，说明 12/16 px，标题 20/26 px | 当前标题偏重，正文与技术字段混杂；建立统一文字层级，中文优先采用系统字体 |
| 页面布局 | 标准内容最大宽度 720 px、宽页 820 px；侧栏 232 px、导航行 32 px；页边距与段落间距共享 | 当前内容铺满窗口、会话按钮块较大；固定阅读宽度，紧凑侧栏，统一对齐与留白 |
| 颜色与表面 | 当前主窗口为白色画布、浅灰侧栏、细边框；选中和悬停用低强度填色 | 当前整页偏绿、反复出现同类白卡片；采用中性色基底，强调色用于必要状态和操作 |
| 设置与管理 | 共用侧栏、页面介绍、分组、设置行组件；一行内左侧名称/说明、右侧控件 | 用结构化摘要、分组行和详情面板表达管理数据，避免全部展示成文本卡片 |
| 记忆与历史 | 记忆页标题/说明、活动概览；历史按日期组织，支持折叠、加载更早记录、空态与读取状态 | 时间线应先回答“发生了什么”；记忆先展示可读内容与来源，再展开技术字段 |
| 交互细节 | 选中态、键盘焦点、禁用态、滚动位置恢复；弹层支持 Escape、点击外部关闭和焦点恢复 | 将加载、断连、空数据、选中、详情打开/关闭等纳入实际窗口验收 |

这些做法在 Electron 中实现，但决定效果的是视觉规范、组件与内容组织。BoxAgent 已有 Electron 骨架，可以在现有 Engine 接口上改造管理页。

## 管理面板的第一版目标

- 统一外壳：紧凑侧栏、当前会话入口、连接状态与页面标题；共用文字、间距、圆角和控件规范。
- 有效上下文：顶部给出构成和总量，按角色指令、正式历史、本轮参考分组；条目正文可读，来源、有效期与完整内容在详情中展示。
- 事件时间线：按时间和回合组织对话、参考、任务；默认给出事件摘要，展开后查看关联与原始记录。只使用实际记录的字段。
- 长期记忆：优先显示记忆正文、类型与已有来源；与本轮召回区分。诊断页用级别、时间、摘要组成行，详情保留原始字段。
- 截图验收覆盖有数据、空数据、加载、断连、长内容与窄窗口；同时检查会话切换、页面切换、滚动、键盘与详情关闭。

UI-P1 的数据接入已完成。后续已实现管理面板视觉与交互第一版，见 [改造验收](<2026-10-12-管理面板视觉改造验收.md>)，待用户体验反馈。UI-P2 的桌宠与对话小窗也使用同一套规范，分别验收实际窗口效果。

## 证据与边界

- [共用设计参数](/Users/bytedance/MiraJelly/app/src/design-system/tokens.css)、[当前浅色主窗口](/Users/bytedance/MiraJelly/app/src/design-system/main-window-light.css)、[侧栏组件](/Users/bytedance/MiraJelly/app/src/design-system/AppSidebar.tsx)。
- [设置分组组件](/Users/bytedance/MiraJelly/app/src/routes/settings/SettingsPrimitives.tsx)、[设置页](/Users/bytedance/MiraJelly/app/src/routes/settings/SettingsPage.tsx)、[历史页](/Users/bytedance/MiraJelly/app/src/history/ComputerHistorySection.tsx)、[记忆页头](/Users/bytedance/MiraJelly/app/src/memory/MemoryHeader.tsx)。
- [9 月 28 日浅色主题验收记录](/Users/bytedance/MiraJelly/docs/testing/reports/2026-09-28-light-theme-finalization-report.md)记载实际 Electron 检查；本次未复跑。检查时未发现 5173/1734 预览端口监听。
- 查看了 [9 月 4 日设置截图](/Users/bytedance/MiraJelly/docs/design-qa/computer-use-general-2026-09-04.png)和 [记忆探索截图](/Users/bytedance/MiraJelly/docs/design-explorations/main-app-recording/evidence/memory-current-desktop.png)。两者属于历史材料，不能作为当前浅色应用实况。
- 参考的部分设计参数文件注明来自其他应用包。采用布局、层级与交互原则，在 BoxAgent 内编写自己的样式与组件；不搬运整套资产或字体文件。
