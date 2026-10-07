# BoxAgent 运行记录与上下文审计

BoxAgent 将“可复现的任务运行记录”和“进程诊断日志”分开保存。开发环境的默认数据根目录是仓库内 `.runtime/pet/`；正式应用使用应用数据目录。`--log-dir` 只改变下文的诊断日志根目录，不再改变任务运行记录的位置。

## 目录

```text
<BOXAGENT_DATA_DIR>/
├── runs/
│   ├── latest.json
│   ├── latest-run -> YYYY-MM-DD/HHMMSS-<task_id>/
│   ├── index.jsonl
│   ├── by-session/<session_id>/       # Finder 中的当前会话视图
│   │   └── YYYY-MM-DD-HHMMSS-<task_id> -> ../../YYYY-MM-DD/HHMMSS-<task_id>/
│   └── YYYY-MM-DD/
│       └── HHMMSS-<task_id>/
│           ├── manifest.json
│           ├── context.json
│           ├── request.json
│           ├── memory-retrieval.json
│           ├── events.jsonl
│           ├── result.json
│           └── step-*.{txt,jpg}
├── conversations/sessions/<session_id>/
│   ├── events.jsonl
│   ├── context.jsonl
│   └── runtime-bindings.json
└── logs/
    ├── engine.log
    ├── runtime/qwen/events.jsonl
    ├── runtime/codex/
    ├── memory/events.jsonl
    ├── memory/worker.log
    └── context/checkpoints.jsonl
```

菜单中的“打开当前会话最近运行…”只会定位当前 Product Session
的最新任务；“打开当前会话运行记录…”会打开 `by-session/<session_id>/`
生成的只读索引视图。新会话尚未执行任务时，该目录为空，不会退回上一会话的
`latest-run`。“打开诊断日志…”仍打开全局进程日志目录。

## 单次任务如何验收

- `manifest.json`：任务 ID、时间、Session、Interaction、Provider、Model 和原始目标。
- `context.json`：Harness 看到的当前 Query、环境、历史、增量历史、记忆条目和最终 Evidence Context。
- `request.json`：真正交给 Runtime 的完整请求合同，包括 Developer Instructions、历史、记忆证据、输出 Schema 和工具定义。
- `memory-retrieval.json`：Jev-Mem 的 Query、模式、Top-K、耗时、降级原因、Profile、Fact/Narrative 结果和最终注入条目。
- `events.jsonl`：按时间追加的生命周期、RPC、工具动作和耗时事件；`runtime_request_sent` 是 Codex App Server 实际发送的增强输入。

所有 JSON/JSONL 写盘前都经过统一脱敏；API Key、Authorization、Token、密码、验证码和内联图片不会原样进入审计文件。

## Qwen Realtime

Qwen 的实际非音频 WebSocket 请求记录在 `logs/runtime/qwen/events.jsonl`，事件名为 `client.request`。可以直接检查：

- `session.update` 中的稳定指令和工具；
- 恢复的 Profile、Checkpoint 与 user/assistant 历史；
- 每轮动态环境与长期记忆 Context；
- 当前 User Message 与 `response.create` 的先后顺序。

音频分片不会写入日志。

## 记忆图可视化

记忆看板仍是原生 AppKit 窗口，中间的关系图改由本地 `WKWebView + vis-network 9.1.9`
渲染。HTML 和 JavaScript 位于 `assets/memory-graph/`，不启动 HTTP 服务，不访问 CDN。
图中支持力导向布局、平行曲线边、关系类型筛选、Hover、缩放、平移、拖动节点和双击聚焦。
点击节点仍通过 WebKit Message Handler 回到原生详情面板；删除、搜索和记忆 API 边界未变。

## 兼容旧目录

旧版本可能把任务写在 `<BOXAGENT_DATA_DIR>/tasks/` 或 `<log-dir>/tasks/`。这些目录不会自动搬迁或删除，Skill 沉淀读取器仍会兼容查找；新任务统一进入 `runs/YYYY-MM-DD/HHMMSS-<task_id>/`。
