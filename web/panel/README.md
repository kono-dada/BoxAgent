# 会话管理面板

先启动桌宠 `uv run --script scripts/pet.py`，再在仓库根目录运行：

```bash
pnpm --dir web/panel install
pnpm --dir web/panel start
```

面板只读：查看会话、当前有效上下文、请求快照、事件时间线、长期记忆和最近错误。
浅色侧栏导航与会话列表共用统一样式。上下文按类别分组，事件按日期排列，
记忆优先显示正文，诊断按级别和摘要展示。搜索过滤当前页已载入的记录。
Engine 断线后自动重连；可用刷新按钮更新内容。点击条目打开详情，Escape 或
点击遮罩关闭并恢复焦点。页面切换保留搜索词和滚动位置。
没有模型请求、电脑操作或麦克风调用。tokens 是估计，不能由构成或延迟推断 KV 缓存命中。

默认连接仓库 `.runtime/pet/engine.sock`；可用 `BOXAGENT_DATA_DIR` 指定其他数据目录。
`pnpm --dir web/panel test` 验证 socket 消息分片、请求关联与事件响应结构。
`pnpm --dir web/panel start -- --smoke` 在真实 Electron 中检查页面、搜索、
详情关闭、快速切换、空会话、滚动恢复和窄窗口，保存到
`/tmp/boxagent-panel-visual/` 后退出；可用 `BOXAGENT_PANEL_CAPTURE_DIR` 改输出目录。
兼容保留 `/tmp/boxagent-panel-smoke.{json,png}`。

本轮验收见 [管理面板改造记录](../../docs/需求/2026-10-12-管理面板视觉改造验收.md)。
