# BoxAgent 运行记录与上下文审计

BoxAgent 将“可复现的任务运行记录”和“进程诊断日志”分开保存。开发环境的默认数据根目录是仓库内 `.runtime/pet/`；正式应用使用应用数据目录。`--log-dir` 只改变下文的诊断日志根目录，不再改变任务运行记录的位置。

## 目录

```text
<BOXAGENT_DATA_DIR>/
├── sessions/
│   ├── index.json
│   ├── .migrated-v1                 # 旧安装迁移完成标记；新安装不需要
│   └── <session_id>/
│       ├── state.json               # 元数据、Runtime 绑定和游标
│       ├── session.v1.jsonl         # 正式事实、wire 账本、Checkpoint、任务引用
│       └── tasks/<task_id>/
│           ├── manifest.json
│           ├── context.json
│           ├── request.json
│           ├── memory-retrieval.json
│           ├── events.jsonl
│           ├── result.json
│           └── step-*.{txt,jpg}
├── attachments/v1/objects/          # 新任务截图按内容哈希存储，轨迹保存引用
├── conversations/                  # 未迁移安装使用；迁移保留源目录
├── conversations-unified-rollback/ # 回滚时导出的最新兼容投影
├── runs/                           # 兼容布局任务 + by-session/<id>/ Finder 索引
└── logs/
    ├── errors.jsonl            # 所有进程的 warning 及以上；排障先看这里
    ├── desktop.jsonl           # 桌宠宿主进程诊断（info 及以上）
    ├── engine.jsonl            # Engine 进程诊断（info 及以上）
    ├── crash/<进程>.log         # faulthandler：原生崩溃与硬故障
    ├── legacy/                 # 早期写在数据根目录的旧日志，启动时迁入
    ├── engine.log              # Engine 子进程原始 stdout/stderr
    ├── events.jsonl            # 宿主收到的 UI 事件流
    ├── runtime/qwen/events.jsonl
    ├── runtime/codex/
    ├── memory/events.jsonl
    ├── memory/worker.log
    └── context/checkpoints.jsonl
```

诊断日志的格式、轮转和排障方法见 [INFRA-03 统一日志](<需求/INFRA-03-统一日志.md>)。

伙伴控制台「任务与运行记录」只列当前 Product Session 的任务，支持打开指定运行、最近运行与会话索引。Finder 索引位于 `runs/by-session/<session_id>/`，兼容统一任务和旧任务；迁移副本按 task ID 去重。新会话尚未执行任务时索引为空，不会退回上一会话。「设置 → 诊断日志」打开全局日志目录。原菜单只保留控制台、新建会话与退出。

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

按 Session 的“模型可见上下文”记录在统一 `session.v1.jsonl` 中的 Context Ledger 事件；未迁移安装仍使用 `conversations/sessions/<ses_id>/context.v1.jsonl`。Engine 通过 `context_snapshot` / `context_entry` 提供构成、回合时延和原文。当前 Query 参考包合并记忆、决策和环境，下一 Query 删除旧包并记录 provider 回执；正式消息保留。格式与指标定义见 [REQ-01](<需求/REQ-01-上下文可观测.md>) 和 [参考生命周期](<需求/REQ-02-03-Query参考包生命周期.md>)。

## 记忆图可视化

记忆图已迁入 `web/panel` 的 React + vis-network 页面，支持关系图/列表、类型分层、
平行曲线边、关系筛选、缩放、拖动、双击聚焦和邻居探索。节点详情、删除与图查询通过
Engine IPC 完成。旧 AppKit 记忆窗口和独立 `assets/memory-graph/` WebKit 资源已删除。
控制台设置提供中英文及浅色、深色、跟随系统，图标签和画布外观同步变化；记忆正文保持原文。

## 兼容旧目录

新安装直接用 `sessions/`；已有 `conversations/index.json` 的安装需退出 Engine 后显式迁移，迁移复制并验证任务文件，源目录保留。未迁移/回滚时新任务仍用 `runs/YYYY-MM-DD/HHMMSS-<task_id>/`；Skill 读取器兼容更早的 `tasks/`、`logs/tasks/`。迁移与回滚命令见 [安装文档](setup.md#旧会话迁移与回滚)。回滚读取最新正式历史投影，不撤销电脑动作或删除长期记忆。
