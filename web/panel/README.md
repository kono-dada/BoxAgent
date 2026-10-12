# 伙伴控制台

完整应用使用统一入口：

```bash
./scripts/install.sh
./scripts/run-pet.sh
```

Electron 主进程管理透明桌宠、React 对话小窗和按需创建的伙伴控制台。关闭控制台保留桌宠、共享 Engine 连接和本机服务；再次打开创建控制台窗口。小窗宽 440px、空闲约 328px，随内容展开至最多 600px，长历史内部滚动；角色动作的边界裁剪不再定位小窗，内容展开时优先保持标题位置。“最新消息”入口占独立一行，不覆盖正文。背景为半透明程序化流光，语音状态调节强度；隐藏和减少动态效果时暂停。收起小窗只隐藏，不销毁消息与草稿。重复启动复用同一个 Electron 实例。

应用身份为 BoxAgent。`scripts/launch.cjs` 在 `.runtime/desktop/BoxAgent.app` 准备本机产品 bundle，设置显示名、进程名、bundle ID 和图标，保留依赖目录里的 Electron.app；源或图标变化时重建，否则直接复用。完整 `.command` 启动与独立 `pnpm start` 均使用此入口。它是源码运行环境，完整启动仍通过 `.command` 注入 Python/Engine 参数，不是可独立分发的安装包。

图标源位于 `public/brand/`：白底、奶油色小猫从纸盒探头的 SVG，PNG / ICNS 用于 Dock/控制台，单色 template 图标用于菜单栏。执行 `pnpm exec electron scripts/render-icons.cjs` 可离线重新导出。MiraJelly 的命名、Dock 与 template 图标处理作为实现参考，未复制其图标。

开发时 `pnpm --dir web/panel build` 检查 TypeScript 并构建三个 HTML 入口；`pnpm --dir web/panel start` 可独立打开只连接 Engine 的控制台。独立模式的桌面专属功能提示启动完整桌宠。启动不重新构建；修改后运行 build。安装脚本包含前端依赖与构建，日志在 `<数据目录>/logs/desktop-service.log`；Engine 日志尊重 `--log-dir`。

统一使用 React 18 + TypeScript + Vite，自定义 CSS，关系图使用 vis-network；消息使用 react-markdown + remark-gfm，禁止原始 HTML。VRM 复用 `web/vrm` 的 Three.js / three-vrm 播放器。

## 前端代码组织

- `src/App.tsx`：导航、选中会话、页面加载与连接状态；保存各页搜索和滚动位置。
- `src/data.ts` / `src/types.ts`：按页面集中读取 IPC 数据，明确页面和桥接的数据契约。
- `src/pages/`：对话、形象、角色、Skill、运行记录、设置、时间线与诊断；上下文和记忆图继续使用独立组件。
- `src/components/`：侧栏、页面分发、详情弹窗、空态及展示工具。
- `src/companion/`：对话小窗与透明桌宠 renderer；`components/TaskStatus.tsx` 在小窗和控制台共用。
- `desktop/host.cjs`：窗口、裁剪、拖动、菜单、通知与形象加载；`service-client.cjs`：Python stdio 服务。
- `src/hooks/useAction.ts`：异步操作的忙碌状态和成功/失败反馈。
- `src/pages.ts` / `src/event-model.ts`：导航元数据和新旧事件格式归一化。

页面切换卸载旧页并丢弃迟到结果；搜索与界面偏好变化只重绘，保留当前页草稿。Engine 状态直接作为 props 更新；preload 的状态/诊断订阅返回解绑函数。原 `renderer.js`、二次挂载桥和功能页聚合文件已移除。

- 有效上下文：统计、分类构成、请求总量/增量趋势，以及联动的上下文浏览器。悬停预览、点击固定请求；分类和条目可展开，读取完整内容。
- 长期记忆：真实节点与有向关系图、自由/类型分层布局、拖动/缩放/双击聚焦、节点与关系筛选、标签开关、邻居展开、只看邻居与列表。右侧展示节点字段和可跳转的直接关系。
- 时间线与诊断：按日期/级别组织已有记录，详情保留原始字段。

面板包含原菜单的完整操作入口，采用明确的 Engine/桌面宿主 IPC 白名单。搜索过滤当前页已载入的记录；刷新重新读取。
断线后自动重连，空态提供恢复入口；详情支持 Escape 和焦点恢复。
浏览管理数据不会触发模型或麦克风。对话发送、麦克风和屏幕总结由用户点击后调用原有 Engine 功能。

上下文请求仅覆盖当前模型连接，历史条目借最近 500 条事件恢复；缺失时显示已知下限，增量不作确定值。tokens 是估算，实际计费、KV 缓存命中与窗口容量尚无接口数据。记忆初始最多 100 个节点/500 条关系，可展开直接邻居；类型分层反映记录的类型，当前数据全为 EVENT 时只会有一层。

默认连接 `.runtime/pet/engine.sock`；`BOXAGENT_DATA_DIR` 可指定其他目录。

```bash
pnpm --dir web/panel test
pnpm --dir web/panel run build
pnpm --dir web/panel exec electron . --smoke
```

测试覆盖 socket/事件结构、历史请求重建与真实 Electron 交互，截图和报告保存到 `/tmp/boxagent-panel-visual/` 后退出。`BOXAGENT_PANEL_CAPTURE_DIR` 可改输出目录。使用不存在的 `BOXAGENT_DATA_DIR` 可检查离线状态。

参考与验收见 [记忆图与上下文控制台](../../docs/需求/2026-10-12-记忆图与上下文控制台验收.md)。

## 导航与设置

默认打开对话页。管理页面随可用窗口宽度展开；3D 预览和记忆图随视口高度扩展，形象选择栏保持紧凑；对话消息保留阅读宽度。主导航保留对话与会话、长期记忆、Skill 管理、任务与运行记录；「观察」分组保留有效上下文和事件时间线。
左下角固定「设置」入口，与右侧简短连接状态横排，完整 Engine 状态可悬停查看；包含概览、界面偏好、3D 形象、角色设定、语音与桌面、诊断日志。概览卡片与分类标签均可进入各页；返回按钮恢复进入设置前的页面、搜索和滚动位置。
麦克风与桌面小窗也在对话页提供快捷操作；授权/停止在任务页。原菜单的功能全部可达，详细分组见 [设置与主导航整理](../../docs/需求/2026-10-12-设置与主导航整理.md)。

## 原菜单功能

- 对话与会话：显示/收起桌面小窗、新建/激活/归档会话、查看正式消息与发送文字。
  会话工具栏左侧显示当前/历史状态，操作集中在右侧；新建会话使用主按钮，麦克风显示启用状态，桌面小窗使用简短文案及完整提示。窄窗口自动换行，中英文及深浅色共用同一布局。
- 3D 形象：实时 VRM 预览、选择并应用本地形象；宿主预加载成功后才替换和保存。
- 角色设定：修改名称与 SOUL.md，保存并应用。
- Skill 管理：内置 Skill 启停；用户 Skill 新建、编辑、删除。
- 任务与运行记录：当前状态、操作授权、停止任务；打开最近运行、会话目录或指定产物。
- 设置 → 语音与桌面：麦克风、屏幕总结、小窗、诊断目录、退出桌宠/Engine。
- 长期记忆：关系探索与删除选中记忆。归档/删除/退出使用界面确认。

原状态栏和桌宠右键菜单保留「伙伴控制台…」「新建会话…」「退出 BoxAgent」，配合原生符号图标和退出分组，其他重复功能已移除；重复打开会唤起已开的窗口。关闭面板保留桌宠和 Engine。
React 对话小窗在上滑阅读历史时显示右下角的「回到最新消息」箭头；点击后定位回复底部并继续跟随新回复。手动滚到底部也会隐藏按钮；输入焦点和草稿保持。
桌面专属功能由同一 Electron Host 处理；资源/文件操作转给 Python DesktopService。旧 AppKit UI、WebKit wrapper、desktop.sock 与主线程队列已移除。


隔离写入验收使用真实 Electron 渲染器和 IPC 替身，不修改实际会话/Skill：

```bash
pnpm --dir web/panel exec electron tests/feature-smoke.cjs
pnpm --dir web/panel exec electron tests/lifecycle-smoke.cjs
pnpm --dir web/panel exec electron tests/avatar-bridge-smoke.cjs
```

完整菜单映射与验证见 [菜单功能迁移验收](../../docs/需求/2026-10-12-菜单功能迁移验收.md)。

## 界面偏好

设置 → 界面偏好提供中文 / English，以及浅色 / 深色 / 跟随系统。选择即时生效并保存在面板的本地存储，重开面板后恢复；跟随系统响应系统明暗变化。语言覆盖导航、设置、面板操作、记忆图标签和上下文统计/请求浏览器，用户会话、记忆、角色及原始日志内容保持原文。此设置同时作用于控制台和小窗，使用同源 storage 事件同步；桌宠背景保持透明。

控制台深色主题使用中性黑灰底、浅灰文字与白色主按钮，3D 预览背景同步；小窗继续使用原有蓝灰底和柔光效果。启动或重连期间，尚未发送的 Engine 请求最多等待连接 5 秒；已发送的操作断线后直接报错，不自动重放。恢复状态后清除连接错误提示，保留其他操作失败信息。

全部 AppKit 窗口代码已删除；macOS 截屏、音频与 Computer Use 仍使用既有后端能力。

```bash
pnpm --dir web/panel exec electron tests/preferences-smoke.cjs
```

## 与后端的连接

页面通过 `window.boxagent.request(method, params)` 调用 API。preload 用 Electron IPC 转给主进程，主进程按白名单连接 Python `engine.sock`，或通过 stdio 调用非 UI 的 DesktopService；Socket 使用带请求 ID 的 JSONL RPC，并支持 Engine 状态事件推送。业务 API 不走 HTTP；3D 预览资源由本机回环 HTTP AssetServer 提供。诊断日志尾部由主进程读取固定路径。完整时序、通道、权限和生命周期见 [系统架构](../../docs/BoxAgent-架构重构方案.md#44-伙伴控制台与前后端连接)。

角色预览 iframe 通过初始主题参数与主题消息同步背景；跟随系统变化不重载模型。独立桌宠播放器继续保持透明背景。

「设置 → 3D 形象」的桌宠大小滑块支持 50%～600% 连续调整、当前百分比和恢复默认。前端通过 `desktop_set_pet_scale` 请求本机 Host，合并连续拖动的待处理值，按顺序发送；偏好保存到数据目录 `appearance.json`，重启与换装保留。菜单继续保留控制台、新建会话和退出三项。


完整桌面迁移隔离验收：

```bash
pnpm --dir web/panel exec electron tests/desktop-smoke.cjs
```

使用真实 Electron Host、VRM 与 Python 资源服务、替身 Engine。覆盖窗口生命周期、换装失败保留、缩放、消息 Markdown / 滚动、会话草稿、任务授权 / 停止、语音开关 IPC、拖动和单实例；不启用真实音频、模型或电脑操作。截图在忽略目录 `artifacts/electron-desktop/`。
