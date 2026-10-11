# 会话管理面板

先启动桌宠 `uv run scripts/pet.py`，再在仓库根目录运行：

```bash
pnpm --dir web/panel install
pnpm --dir web/panel start
```

面板只读：查看会话、当前有效上下文、请求快照、事件时间线、长期记忆和最近错误。
Engine 断线后自动重连；可用刷新按钮更新内容。点击内容卡片查看完整内容。
没有模型请求、电脑操作或麦克风调用。tokens 是估计，不能由构成或延迟推断 KV 缓存命中。

默认连接仓库 `.runtime/pet/engine.sock`；可用 `BOXAGENT_DATA_DIR` 指定其他数据目录。
`pnpm --dir web/panel test` 验证 socket 消息分片与请求关联。
`pnpm --dir web/panel start -- --smoke` 捕获自身页面到 `/tmp/boxagent-panel-smoke.{json,png}` 后退出。
