# FIX-01 屏幕摘要复读与截图失败

> 分支：`fix/window-summary`（基于 `feature/logging`）
> 状态：已实现，278 passed（基线 274 + 新增 4）

## 0. 结论

1. **截图失败**：一次 `screencapture` 失败就把整个桌面观察停掉，并提示“请检查屏幕录制权限”。实际原因不是权限问题。
   - 现在单次失败只跳过这一轮，**连续 3 次**失败才停止。
   - 透明窗口（`alpha = 0`）不再被选为截图目标。
2. **摘要复读**：0.8B 模型用贪心解码，偶尔把同一句话重复到 512 token 上限。
   - 解码加 `repetition_penalty=1.15`（窗口 64 token），`max_tokens` 从 512 降到 256。
   - 输出后再按句去重，并丢弃被截断的半句，作为兜底。

## 1. 截图失败

### 1.1 现象

INFRA-03 上线后，`errors.jsonl` 记录到：

```
desktop observation stopped
RuntimeError: could not create image from window
```

前台应用是 DSH Desktop。桌宠随即显示“桌面观察已暂停，请检查屏幕录制权限及本地模型环境”，此后不再自动恢复。

### 1.2 复现与原因

| 实验 | 结果 |
|---|---|
| 对 DSH Desktop 的 12 个窗口逐个 `screencapture -l` | 2 个返回 `could not create image from window`，其余成功 |
| 失败窗口之一 | `alpha = 0.0`，高 32 px，位于屏幕外（`Y = −32`），是透明辅助窗口 |
| 另一个失败窗口（1920×1080 主窗口） | 稍后重复 3 次，全部成功 |
| 屏幕录制权限 | 正常：同一进程能截其他应用 |

结论：

1. **失败是瞬时的**：窗口在“列举”和“截图”之间被重绘、缩放或切换时，系统会拒绝一次。
2. **透明窗口必然失败**：旧筛选条件只看 layer 和尺寸，可能选中 `alpha = 0` 的窗口。
3. **旧代码把一次失败当成致命错误**，并给出错误的权限提示。

### 1.3 修改

| 项 | 修改 |
|---|---|
| 窗口筛选 | `focused_window()` 增加 `kCGWindowAlpha > 0` |
| 失败处理 | 单次失败记录 warning，发出 `context.waiting`（“前台窗口暂时无法截图，稍后重试”），下一轮重试 |
| 停止条件 | 连续 `MAX_CAPTURE_FAILURES = 3` 次失败才抛出，沿用原有暂停流程；任一次成功清零计数 |

## 2. 摘要复读

### 2.1 历史数据

统计 `logs/events.jsonl` 与 `logs/legacy/events.jsonl` 中全部 98 条 `context.updated`：

| 指标 | 值 |
|---|---:|
| 含重复句的摘要 | 8 条（8.2%） |
| 长度 p50 / p90 / max | 731 / 1313 / 2136 字 |

典型复读：“文档中提到了“AgentDocEn2”和“AgentDocEn1”，并描述了它们之间的协作关系。”重复 8 次以上，直到被截断。

### 2.2 离线对比

在 12 张本机窗口截图上（2 个窗口 × 6 种裁剪，960 px），对 4 组解码参数做对比。指标定义：

- **句重复**：同一句（长度 > 4 字）出现 ≥ 2 次的样本数；
- **12-gram 重复率**：$r = \dfrac{\sum_g (c_g - 1)\,[c_g > 1]}{N_{\text{gram}}}$，其中 $c_g$ 是 12 字片段 $g$ 的出现次数。

| 配置 | 耗时 p50 | 字数 p50 | 字数 max | 句重复样本 | 12-gram 重复率均值 |
|---|---:|---:|---:|---:|---:|
| 基线：512 token，无惩罚 | 0.8 s | 165 | 1127 | 1 | 0.095 |
| `rp=1.1` | 0.8 s | 184 | 738 | 1 | 0.053 |
| `rp=1.1`，256 token | 0.7 s | 184 | 479 | 1 | 0.035 |
| **`rp=1.15`，选用** | 0.7 s | 176 | 271 | **0** | **0.002** |

`rp=1.15` 消除了复读，正常摘要长度和耗时不变，内容质量与基线相当（均能指出应用名、主要面板和关键文字）。上线配置为 `rp=1.15` 加 256 token 上限：离线最长输出只有 129 token，256 只作为保险。

### 2.3 输出兜底

`clean_summary()`：

1. 按 `。！？!?` 和换行切句，忽略结尾标点后去重，保留首次出现的顺序；
2. 若最后一句没有结束标点，且前面已有完整句子，视为截断残句并丢弃；
3. 有清理时记录 `summary cleaned`（info），带原始字数和清理后字数。

基线中最差的样本经兜底后从 1127 字降到 330 字；`rp=1.15` 的输出无需清理。

## 3. 测试

| 用例 | 覆盖 |
|---|---|
| `test_transient_capture_failure_skips_round_until_limit` | 前 2 次失败跳过，第 3 次抛出 |
| `test_successful_capture_resets_failure_count` | 成功后计数清零 |
| `test_repeated_sentences_are_removed_in_order` | 按句去重并保持顺序 |
| `test_truncated_tail_is_dropped_but_single_fragment_kept` | 丢弃截断残句；只有一句时保留 |

## 4. 实机验证

| 时间 | 事件 | 结果 |
|---|---|---|
| 18:40:04 | 前台为“豆包工作”，截图失败一次 | 记录 `window capture failed (1/3)`，显示“前台窗口暂时无法截图，稍后重试”，观察继续运行；修复前同样的失败会让观察永久停止 |
| 18:44 起 | 屏幕锁定，前台为 `loginwindow` | `focused_window()` 返回空，持续 `context.waiting`，符合预期 |

解锁后的摘要质量尚未实机观察，可用下面的命令统计清理频率：

```bash
jq -c 'select(.msg=="summary cleaned") | .data' .runtime/pet/logs/engine.jsonl
```

## 5. 已知限制

1. 离线样本只有 12 张，来自 2 个窗口；需要上线后看 `summary cleaned` 的出现频率再确认。
2. 重复惩罚也会抑制合理的重复词（例如多次出现的项目名）。在对比样本里没有观察到信息丢失。
