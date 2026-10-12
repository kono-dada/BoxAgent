# 会话管理面板

先启动桌宠 `uv run --script scripts/pet.py`，再在仓库根目录运行：

```bash
pnpm --dir web/panel install
pnpm --dir web/panel start
```

`start` 先执行 TypeScript 检查与 Vite 构建，再由 Electron 载入 `dist/index.html`。
记忆和上下文页使用 React 18 + TypeScript；关系图使用 vis-network 9.1.9。
共用外壳、时间线和日志仍是原生 JavaScript，样式为自定义 CSS。

- 有效上下文：统计、分类构成、请求总量/增量趋势，以及联动的上下文浏览器。悬停预览、点击固定请求；分类和条目可展开，读取完整内容。
- 长期记忆：真实节点与有向关系图、自由/类型分层布局、拖动/缩放/双击聚焦、节点与关系筛选、标签开关、邻居展开、只看邻居与列表。右侧展示节点字段和可跳转的直接关系。
- 时间线与诊断：按日期/级别组织已有记录，详情保留原始字段。

面板只读，复用 Engine IPC 白名单。搜索过滤当前页已载入的记录；刷新重新读取。
断线后自动重连，空态提供恢复入口；详情支持 Escape 和焦点恢复。
没有模型请求、电脑操作或麦克风调用。

上下文请求仅覆盖当前模型连接，历史条目借最近 500 条事件恢复；缺失时显示已知下限，增量不作确定值。tokens 是估算，实际计费、KV 缓存命中与窗口容量尚无接口数据。记忆初始最多 100 个节点/500 条关系，可展开直接邻居；类型分层反映记录的类型，当前数据全为 EVENT 时只会有一层。

默认连接 `.runtime/pet/engine.sock`；`BOXAGENT_DATA_DIR` 可指定其他目录。

```bash
pnpm --dir web/panel test
pnpm --dir web/panel run build
pnpm --dir web/panel exec electron . --smoke
```

测试覆盖 socket/事件结构、历史请求重建与真实 Electron 交互，截图和报告保存到 `/tmp/boxagent-panel-visual/` 后退出。`BOXAGENT_PANEL_CAPTURE_DIR` 可改输出目录。使用不存在的 `BOXAGENT_DATA_DIR` 可检查离线状态。

参考与验收见 [记忆图与上下文控制台](../../docs/需求/2026-10-12-记忆图与上下文控制台验收.md)。
