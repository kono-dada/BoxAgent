# UI-REF-01 控制台前端结构整理

基于已合入的 PR #6 / `main` @ `acdfe12`，分支 `refactor/panel-components`。范围限控制台前端结构；保留原 API、功能、CSS 和依赖。

## 实现

- 全部页面由单一 React root 管理，移除原生 DOM 外壳、二次挂载桥与集中功能页文件。
- 功能页按职责拆开，统一异步操作反馈；页面 IPC 读取集中在 `data.ts`，用页面类型联合关联视图与数据。
- 时间线兼容新旧事件结构；导航和事件标签在渲染时翻译。
- 搜索、语言与状态推送不卸载当前表单；返回设置前的页面恢复搜索与滚动。切页/切会话丢弃迟到结果。
- IPC 订阅返回解绑函数，解绑不返回 Electron 原生对象；页面卸载释放定时器和监听。

## 验证（2026-10-12）

在仓库根目录运行：

```bash
pnpm --dir web/panel build
pnpm --dir web/panel test
pnpm --dir web/panel exec electron tests/feature-smoke.cjs
pnpm --dir web/panel exec electron tests/preferences-smoke.cjs
pnpm --dir web/panel exec electron tests/lifecycle-smoke.cjs
pnpm --dir web/panel exec electron tests/avatar-bridge-smoke.cjs
pnpm --dir web/panel exec electron tests/pet-scale-smoke.cjs
pnpm --dir web/panel exec electron tests/startup-smoke.cjs
BOXAGENT_DATA_DIR=/tmp/boxagent-refactor-offline pnpm --dir web/panel exec electron . --smoke
```

全部通过：类型/构建、8 项数据测试、原功能操作、25 项语言/主题检查、草稿/导航/迟到响应/详情焦点/解绑回归、预览 iframe 主题与形象应用、连续缩放请求合并、单实例唤起和 5 项离线检查。截图已检查。

操作验收使用 IPC 替身与隔离数据；预览验收使用本机测试 HTTP 页面，没有实际 VRM/Host 参与。本机未启动桌宠，未重跑真实 Host 预览测试。构建仍有 vis-network 大包提示；未增加依赖或扩大到包体优化。Python 后端未改动，未重复 Python 全量测试。

下一条产品主线建议继续 UI-P2：真实对话小窗的语音、回复、任务状态和历史阅读体验；此次整理单独 PR。
