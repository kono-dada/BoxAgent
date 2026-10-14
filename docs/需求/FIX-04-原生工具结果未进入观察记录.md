# FIX-04：原生工具结果未进入观察记录

状态：本地补丁及离线事件回放完成，补丁后的真实操作待验收。保留原有完成证据校验。

## 2026-10-13 用户复验

Product Session `ses_fd8c10942c424fe0` 的“打开音乐。”任务 `48e95d9ca902` 在 18:34:12 启动，18:34:28 被判 `invalid_evidence`。

后台实际产生了两个 `mcpToolCall`：读取 Music 界面，以及点击“自动过渡已打开”提示的“好”。原始 App Server rollout 的调用来自 `mcp__boxagent_cua` 命名空间，其返回包含实际界面文本与截图。最终结果引用了步骤 `[1, 2]`，但 Harness 的观察集合为空，报错详情为“后台结果引用了不存在的观察记录：[1, 2]；实际步骤：[]”。

这不是语音任务没有启动，也不是动态工具丢失：旧线程的 metadata 中仍包含全部 `desktop_*` 动态工具。模型此轮选择原生 MCP 路径，而 Harness 原来只记录 `item/tool/call` 宿主回调路径。

后续用户请求“重新做一下。”产生了独立任务 `b3a8d38a33e0`，其 `desktop_get_app_state` 经过原有工具网关，保存了观察步骤 1，结果通过校验。这轮重试通过发生在补丁之前，不能作为补丁的真机验收。

## 修复

- App Server 同步处理当前 turn 的 `boxagent_cua` 原生工具 started/completed 事件，将实际返回交给任务专属观察回调；观察写入完成后再处理最终结果。
- 工具网关保存同样的步骤、文本、截图附件与成功/失败标记；只记录已发生的操作，不重放动作。
- 当前动态工具清单仍用于匹配操作；不记录旧 turn、其他 MCP 服务、未见 started 的历史 completed 或重复 completed。任务重新绑定时清除原生调用缓存。
- 原生工具失败仍记作失败观察，不能证明任务完成；不存在的步骤仍被 `invalid_evidence` 拒绝。

补丁不改变原生 MCP 的调用入口与权限控制，也不重新执行失败任务，不将模型自述替代真实工具返回。

## 固定测试

用例 ID：`FIX-04-native-mcp-observations`，保存在 [native_music_result.json](../../tests/fixtures/native_music_result.json)。界面片段已去除用户账户等信息，事件外壳按当前安装的 App Server schema 构造；不是原始 trace 的完整回放。

[test_executor.py](../../tests/test_executor.py) 通过 App Server 实际读取循环重放两次原生调用、重复完成事件、最终回复及 turn 完成，检查：

- 校验前产生两个真实来源的观察记录，完成结果通过；工具没有被二次调用。
- 文本保存到步骤文件；未知步骤仍拒绝。
- 旧 turn、缺少 started 的结果不入库；原生失败不能证明完成；跨任务缓存清空。

移除原生观察回调时，两次实际工具事件仍不能进入 Harness；最终结果因空观察集合被拒绝，复现本次误报机制。

全量验证：pytest `348 passed, 12 skipped, 13 subtests passed`；Pyright 当前配置范围内 `0 errors, 0 warnings`；`git diff --check` 通过。补丁尚未提交，当前运行中的应用需要重启后才会加载它。
