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
  if (
    compact &&
    (!runtime.task || runtime.task === "idle") &&
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
      <p>{runtime.task_text || t("当前没有任务")}</p>
      {runtime.task_started_at && (
        <p className="metadata">
          {t(active ? "已运行 {0} 秒" : "用时 {0} 秒", elapsed)}
          {active && quiet >= 15 ? ` · ${t("{0} 秒未收到新进展", quiet)}` : ""}
        </p>
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
            {t("停止后台任务")}
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
