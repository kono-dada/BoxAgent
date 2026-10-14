import { useEffect, useState } from "react";
import { t } from "../i18n";
import { run, runtimeLabel, type ActionState } from "../hooks/useAction";
import { taskTiming } from "../companion/model";
import type { RuntimeState } from "../types";
export function TaskStatus({
  runtime,
  busy,
  act,
  compact = false,
}: {
  runtime: RuntimeState;
  busy: boolean;
  act: ActionState["act"];
  compact?: boolean;
}) {
  const [now, setNow] = useState(Date.now() / 1000);
  const active = [
    "accepted",
    "running",
    "awaiting_approval",
    "cancelling",
  ].includes(runtime.task || "");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const { elapsed, quiet } = taskTiming(runtime, now);
  const queue = runtime.task_queue || [];
  if (
    compact &&
    (!runtime.task || runtime.task === "idle") &&
    !queue.length &&
    !runtime.approval &&
    !runtime.error
  )
    return null;
  return (
    <section
      className={`feature-card task-status ${compact ? "compact" : ""}`}
      aria-label={t("当前后台任务")}
    >
      <header>
        <h2>{t("当前后台任务")}</h2>
        <span className={`badge ${active ? "task-active" : ""}`}>
          {runtimeLabel(runtime.task)}
        </span>
      </header>
      {runtime.task_goal && (
        <p className="task-goal" title={runtime.task_goal}>{runtime.task_goal}</p>
      )}
      <p className="task-progress" title={runtime.task_text}>{runtime.task_text || t("当前没有任务")}</p>
      {runtime.task_started_at && (
        <p className="metadata">
          {t(active ? "已运行 {0} 秒" : "用时 {0} 秒", elapsed)}
          {active && quiet >= 15 ? ` · ${t("{0} 秒未收到新进展", quiet)}` : ""}
        </p>
      )}
      {!!queue.length && (
        <details className="task-queue" open={!compact}>
          <summary aria-live="polite">
            <strong>{t("排队中 · {0} 项", queue.length)}</strong>
            <span className="queue-next" title={queue[0].goal}>
              {t("下一项：{0}", queue[0].goal)}
            </span>
          </summary>
          <ol aria-label={t("等待执行的任务")}>
            {queue.map((item, index) => (
              <li key={item.task_id}>
                <span className="queue-position" aria-hidden="true">{index + 1}</span>
                <span>{item.goal}</span>
              </li>
            ))}
          </ol>
          <p className="metadata">{t("当前任务结束后，按此顺序自动执行。")}</p>
        </details>
      )}
      {runtime.approval && (
        <div className="approval-card" role="alert">
          <p>{runtime.approval}</p>
          <div className="feature-actions">
            <button
              className="button primary"
              disabled={busy}
              onClick={() => run(act("answer_approval", { allowed: true }))}
            >
              {t("允许操作")}
            </button>
            <button
              className="button"
              disabled={busy}
              onClick={() => run(act("answer_approval", { allowed: false }))}
            >
              {t("拒绝")}
            </button>
          </div>
        </div>
      )}
      <div className="feature-actions">
        {active && (
          <button
            className="button"
            disabled={busy || runtime.task === "cancelling"}
            onClick={() => run(act("cancel_task"))}
          >
            {t("停止当前任务")}
          </button>
        )}
        {(active || !!queue.length) && (
          <button
            className="button"
            disabled={busy || runtime.task === "cancelling"}
            onClick={() => run(act("cancel_all_tasks"))}
          >
            {t("取消全部任务")}
          </button>
        )}
      </div>
      {runtime.error && (
        <p role="alert" className="feature-status">
          {runtime.error}
        </p>
      )}
    </section>
  );
}
