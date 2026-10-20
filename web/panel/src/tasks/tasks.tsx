import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { taskTiming } from "../companion/model";
import { run, runtimeLabel, useAction } from "../hooks/useAction";
import { useWindowDrag } from "../hooks/useWindowDrag";
import { t } from "../i18n";
import { startPreferences } from "../preferences";
import type { AvatarSnapshot, RuntimeState } from "../types";
import "../../style.css";
import "./tasks.css";

function Tasks() {
  const [runtime, setRuntime] = useState<RuntimeState>({});
  const [desktop, setDesktop] = useState<AvatarSnapshot>({});
  const [now, setNow] = useState(Date.now() / 1000);
  const [, revise] = useState(0);
  const bridge = window.boxagent;
  const action = useAction(bridge);
  const drag = useWindowDrag(bridge);
  useEffect(() => {
    const stops = [bridge.onState(setRuntime), bridge.onDesktop(setDesktop),
      bridge.onDiagnostic((message) => {
        if (message === "Engine 已连接") action.clearConnectionError();
        else if (message === "Engine 已断开") action.setStatus(message);
      })];
    void bridge.request<RuntimeState>("state").then(setRuntime).catch((error) => action.setStatus(error.message));
    void bridge.request<AvatarSnapshot>("desktop_snapshot").then(setDesktop).catch((error) => action.setStatus(error.message));
    const preferences = () => revise((value) => value + 1);
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") bridge.desktopAction("collapse-tasks");
    };
    document.addEventListener("panel-preferences", preferences);
    document.addEventListener("keydown", keyboard);
    return () => {
      stops.forEach((stop) => stop());
      document.removeEventListener("panel-preferences", preferences);
      document.removeEventListener("keydown", keyboard);
    };
  }, []);
  const queued = runtime.task_queue?.length || 0;
  const active = ["accepted", "running", "awaiting_approval", "cancelling"].includes(runtime.task || "");
  const label = runtime.approval ? t("需要你的确认") : runtimeLabel(runtime.task || "idle");
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const { elapsed, quiet } = taskTiming(runtime, now);
  function cancel(method: "cancel_task" | "cancel_all_tasks") {
    run(action.act(method));
  }
  return desktop.tasks_expanded ? (
    <section className="task-float" aria-label={t("任务浮层")}>
      <header className="task-float-header" {...drag}>
        <span className="task-float-brand">BoxAgent <span>{t("任务")}</span></span>
        {desktop.tasks_pinned && <span className="task-pin-indicator" title={t("已固定")}>⌖</span>}
        <button className="icon-button" aria-label={t("收起任务面板")}
          onClick={() => bridge.desktopAction("collapse-tasks")}>×</button>
      </header>
      <main className="task-float-body">
        <section className="task-current" aria-label={t("当前后台任务")} data-active={active}>
          <span className="task-current-icon" aria-hidden="true">{runtime.approval ? "!" : active ? "◌" : "○"}</span>
          <div className="task-current-copy">
            <strong className="task-goal" title={runtime.task_goal}>{runtime.task_goal || t("当前没有任务")}</strong>
            {runtime.task_text && <span className="task-progress" title={runtime.task_text}>{runtime.task_text}</span>}
          </div>
          <span className="task-current-state">{label}</span>
        </section>
        {queued > 0 && <section className="task-queue" aria-label={t("等待执行的任务")}>
          <header><span>{t("接下来")}</span><span>{queued}</span></header>
          <ol>{runtime.task_queue!.map((item, index) => <li key={item.task_id} title={item.goal}>
            <span className="queue-position" aria-hidden="true">{index + 1}</span><span>{item.goal}</span>
          </li>)}</ol>
        </section>}
      </main>
      {runtime.approval && <section className="approval-card" role="alert">
        <p>{runtime.approval}</p>
        <div className="feature-actions">
          <button className="button primary" disabled={action.busy}
            onClick={() => run(action.act("answer_approval", { allowed: true }))}>{t("允许操作")}</button>
          <button className="button" disabled={action.busy}
            onClick={() => run(action.act("answer_approval", { allowed: false }))}>{t("拒绝")}</button>
        </div>
      </section>}
      {(runtime.error || action.status) && <p className="task-float-feedback" role={runtime.error ? "alert" : "status"}>{runtime.error || action.status}</p>}
      <div className="task-actions" aria-label={t("任务操作")}>
        {active && <button disabled={action.busy || runtime.task === "cancelling"}
          onClick={() => cancel("cancel_task")}>{t("停止当前任务")}</button>}
        {(active || queued > 0) && <button disabled={action.busy || runtime.task === "cancelling"}
          onClick={() => cancel("cancel_all_tasks")}>{t("取消全部任务")}</button>}
        <button className="task-pin-button" aria-label={t("固定任务面板")}
          aria-pressed={!!desktop.tasks_pinned}
          onClick={() => bridge.desktopAction("pin-tasks")}>
          {t(desktop.tasks_pinned ? "取消固定" : "固定面板")}
        </button>
      </div>
      <footer className="task-float-footer">
        <span title={active && quiet >= 15 ? t("{0} 秒未收到新进展", quiet) : t("关闭面板不会停止任务")}>
          {runtime.task_started_at ? t(active ? "已运行 {0} 秒" : "用时 {0} 秒", elapsed) : t("关闭面板不会停止任务")}
        </span>
        <button className="text-button" onClick={() => bridge.desktopAction("show-panel")}>{t("全部任务")} ↗</button>
      </footer>
    </section>
  ) : (
    <div className="task-chip" data-active={active} data-attention={!!runtime.approval}>
      <span className="task-chip-grip" title={t("拖动任务入口")} aria-hidden="true" {...drag}>⠿</span>
      <button className="task-chip-button" aria-label={t("展开任务面板")}
        title={runtime.task_goal || t("后台任务")}
        onClick={() => bridge.desktopAction("show-tasks")}>
        <i className="task-chip-dot" aria-hidden="true"/>
        <strong>{label}</strong>
        {queued > 0 && <span className="task-chip-count">{t("排队 {0}", queued)}</span>}
        <span className="task-chip-arrow" aria-hidden="true">↗</span>
      </button>
    </div>
  );
}
startPreferences();
createRoot(document.getElementById("app")!).render(<Tasks/>);
