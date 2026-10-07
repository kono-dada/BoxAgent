# BoxAgent 产品展示与长对话验收

这里固定 BoxAgent V0 的长对话 Hero Journey。场景文件既是产品演示脚本，也是跨版本验收合同；
不要把模型的逐字输出写死，只约束用户意图、能力标签、关键语义和事件顺序。

## 场景

| 文件 | 用途 | 可真实执行 |
|---|---|---|
| `companion-memory.json` | 连续陪伴、自动记忆、画像、跨 Session 召回 | 是 |
| `background-task.json` | 音乐与知乎任务、前后台并行、主动通知、从轨迹创建 Skill | 是，全部为只读或可撤销操作 |
| `voice-safety-management.json` | 全双工语音、打断、记忆/Skill/形象管理页面 | 是 |

三个场景的 capability 并集定义 V0 展示面。`tests/test_product_showcase.py` 会检查场景结构、长对话
长度、能力覆盖和安全边界，避免后续改文案时悄悄丢掉核心能力。

真人一镜到底录制按 [`LIVE_DEMO_SCRIPT.md`](LIVE_DEMO_SCRIPT.md) 执行，其中包含隐私画面、收音、
全双工打断、后台任务、Skill 创建和跨 Session 记忆的逐句脚本。

## 生成视频

```bash
uv run --script scripts/render_product_showcase.py
```

输出到 `.runtime/product-showcase/`：

- `boxagent-product-showcase.mp4`：静音、带中文字幕的 16:9 演示视频；
- `slides/`：每个镜头的 PNG，便于替换或重新剪辑；
- `manifest.json`：视频所用场景、能力和本机真实 E2E 证据来源。

视频使用仓库真实桌宠素材、当前产品对话样式、真实管理界面，以及音乐与知乎任务的
最终截图。它是可重复生成的产品展示，不冒充一镜到底的真人录屏。真实 Provider 验收仍以
Session Event、任务证据和 Notification Outbox 为准。
