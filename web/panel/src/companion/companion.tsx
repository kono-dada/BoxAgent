import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type PointerEvent,
} from "react";
import { createRoot } from "react-dom/client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { t } from "../i18n";
import { locale, startPreferences } from "../preferences";
import { useAction, run } from "../hooks/useAction";
import { TaskStatus } from "../components/TaskStatus";
import { transcript } from "./model";
import type { AvatarSnapshot, ProductMessage, RuntimeState } from "../types";
import "../../style.css";
import "./companion.css";
const bridge = window.boxagent;
function Companion() {
  const [runtime, setRuntime] = useState<RuntimeState>({}),
    [desktop, setDesktop] = useState<AvatarSnapshot & { name?: string }>({});
  const [online, setOnline] = useState(false),
    [history, setHistory] = useState<ProductMessage[]>([]),
    [text, setText] = useState("");
  const [pending, setPending] = useState(""),
    [creating, setCreating] = useState(false),
    [title, setTitle] = useState("");
  const [away, setAway] = useState(false),
    [, revise] = useState(0);
  const drafts = useRef<Record<string, string>>({}),
    liveDraft = useRef("");
  const session = useRef(""),
    request = useRef(0),
    scroll = useRef<HTMLDivElement>(null),
    shell = useRef<HTMLElement>(null),
    content = useRef<HTMLDivElement>(null),
    follow = useRef(true),
    dragging = useRef(false);
  const action = useAction(bridge);
  const refresh = useCallback(async () => {
    const id = session.current,
      mine = ++request.current;
    if (!id) {
      setHistory([]);
      return;
    }
    try {
      const events = await bridge.request<ProductMessage[]>("session_events", {
        session_id: id,
        limit: 1000,
      });
      if (mine === request.current && id === session.current)
        setHistory(events);
    } catch (error) {
      if (mine === request.current)
        action.setStatus(
          error instanceof Error ? error.message : String(error),
        );
    }
  }, []);
  useEffect(() => {
    const state = (value: RuntimeState) => {
      setOnline(true);
      if (value.session_id && value.session_id !== session.current) {
        drafts.current[session.current] = liveDraft.current;
        session.current = value.session_id;
        request.current++;
        setHistory([]);
        setPending("");
        const saved = drafts.current[session.current] || "";
        liveDraft.current = saved;
        setText(saved);
        follow.current = true;
        setAway(false);
        void refresh();
      }
      setRuntime((current) =>
        value.session_id && value.session_id !== current.session_id
          ? value
          : { ...current, ...value },
      );
    };
    const stops = [
      bridge.onState(state),
      bridge.onDesktop(setDesktop),
      bridge.onEvent((event) => {
        if (
          [
            "conversation.updated",
            "session.ready",
            "front.user_text",
            "task.succeeded",
            "task.failed",
            "task.cancelled",
          ].includes(event.type)
        )
          void refresh();
      }),
      bridge.onDiagnostic((message) => {
        if (message === "Engine 已连接")
          void bridge
            .request<RuntimeState>("state")
            .then(state)
            .catch(() => {});
        else if (
          message === "Engine 已断开" ||
          message.startsWith("connect ")
        ) {
          setOnline(false);
          action.setStatus(message);
        } else action.setStatus(message);
      }),
      bridge.onNewSession(() => setCreating(true)),
    ];
    void bridge
      .request<RuntimeState>("state")
      .then(state)
      .catch((error) => action.setStatus(error.message));
    void bridge
      .request<AvatarSnapshot & { name?: string }>("desktop_snapshot")
      .then(setDesktop)
      .catch((error) => action.setStatus(error.message));
    const timer = setInterval(() => void refresh(), 2000);
    const preferences = () => revise((value) => value + 1);
    document.addEventListener("panel-preferences", preferences);
    return () => {
      stops.forEach((stop) => stop());
      clearInterval(timer);
      request.current++;
      document.removeEventListener("panel-preferences", preferences);
    };
  }, [refresh]);
  const entries = transcript(history, runtime, pending);
  const [visible, setVisible] = useState(!document.hidden);
  useEffect(() => {
    const change = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", change);
    return () => document.removeEventListener("visibilitychange", change);
  }, []);
  useLayoutEffect(() => {
    const surface = shell.current,
      body = content.current;
    if (!surface || !body) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const measure = () => {
      if (timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        const scroller = scroll.current!;
        const fixed = Array.from(surface.children)
          .filter(
            (element) =>
              !element.classList.contains("companion-history-wrap") &&
              !element.classList.contains("companion-ambient"),
          )
          .reduce((height, element) => {
            const style = getComputedStyle(element);
            return (
              height +
              element.getBoundingClientRect().height +
              parseFloat(style.marginTop) +
              parseFloat(style.marginBottom)
            );
          }, 0);
        const bodyHeight = Math.min(
          Math.max(64, body.getBoundingClientRect().height + 24),
          runtime.approval ? 140 : 210,
        );
        bridge.desktopAction(
          "resize-bubble",
          Math.ceil(fixed + bodyHeight + 18),
        );
        if (follow.current) scroller.scrollTop = scroller.scrollHeight;
      }, 70);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(body);
    for (const element of Array.from(surface.children))
      if (!element.classList.contains("companion-history-wrap"))
        observer.observe(element);
    measure();
    return () => {
      observer.disconnect();
      clearTimeout(timer);
    };
  }, [
    history,
    runtime.task,
    runtime.approval,
    runtime.error,
    creating,
    desktop.feedback,
    action.status,
  ]);

  useLayoutEffect(() => {
    if (follow.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [
    history,
    runtime.assistant_text,
    runtime.user_text,
    pending,
    action.status,
  ]);
  async function submit() {
    const draft = text.trim();
    if (!draft || !online || action.busy) return;
    const id = session.current;
    setPending(draft);
    setText("");
    liveDraft.current = "";
    drafts.current[id] = "";
    follow.current = true;
    setAway(false);
    try {
      const result = await action.act<{ status: string; message?: string }>(
        "submit_text",
        { goal: draft },
      );
      if (id !== session.current) return;
      if (!["accepted", "succeeded", "completed"].includes(result.status)) {
        setText((current) => {
          const restored = current || draft;
          liveDraft.current = restored;
          drafts.current[id] = restored;
          return restored;
        });
        action.setStatus(result.message || t("未能提交，请重试"));
      }
      await refresh();
    } catch {
      if (id === session.current)
        setText((current) => {
          const restored = current || draft;
          liveDraft.current = restored;
          drafts.current[id] = restored;
          return restored;
        });
    } finally {
      if (id === session.current) setPending("");
    }
  }
  async function create() {
    await action.act<{ session_id: string }>("create_session", {
      title: title.trim() || t("新会话"),
    });
    setCreating(false);
    setTitle("");
    await refresh();
  }
  function drag(event: PointerEvent, phase: string) {
    if ((event.target as HTMLElement).closest("button,input")) return;
    if (phase === "start") {
      if (event.button !== 0) return;
      dragging.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
    } else if (!dragging.current) return;
    bridge.desktopAction("drag", phase);
    if (phase === "end") dragging.current = false;
  }
  return (
    <section
      ref={shell}
      className="companion-shell"
      aria-label={t("伙伴对话")}
      data-activity={
        runtime.user_speaking
          ? "listening"
          : runtime.speaking
            ? "speaking"
            : runtime.voice === "ready"
              ? "voice"
              : "idle"
      }
      data-visible={visible}
    >
      <div className="companion-ambient" aria-hidden="true">
        <i />
        <i />
        <i />
      </div>
      <header
        className="companion-header"
        onPointerDown={(event) => drag(event, "start")}
        onPointerMove={(event) => drag(event, "move")}
        onPointerUp={(event) => drag(event, "end")}
        onPointerCancel={(event) => drag(event, "end")}
      >
        <div className="companion-heading">
          <strong>{desktop.name || t("伙伴")}</strong>
          <span>
            <i className={`companion-dot ${online ? "online" : ""}`} />
            {online
              ? runtime.speaking
                ? t("回复中")
                : runtime.user_speaking
                  ? t("聆听中")
                  : runtime.voice === "ready"
                    ? t("全双工语音已开启")
                    : t("在这里陪你")
              : t("等待 Engine 连接")}
          </span>
        </div>
        <button
          className="icon-button"
          title={t("打开伙伴控制台")}
          aria-label={t("打开伙伴控制台")}
          onClick={() => bridge.desktopAction("show-panel")}
        >
          ▤
        </button>
        <button
          className="icon-button"
          title={t("收起对话")}
          aria-label={t("收起对话")}
          onClick={() => bridge.desktopAction("hide-bubble")}
        >
          ×
        </button>
      </header>
      <div className="companion-history-wrap">
        <div
          className="companion-history"
          ref={scroll}
          onScroll={() => {
            const el = scroll.current!;
            follow.current =
              el.scrollHeight - el.scrollTop - el.clientHeight <= 28;
            setAway(!follow.current);
          }}
        >
          <div ref={content} className="companion-history-content">
            {!entries.length && (
              <div className="companion-welcome">
                <span>✧</span>
                <h1>{t("我在这里")}</h1>
                <p>{t("和我说说今天，也可以让我帮你做点事。")}</p>
              </div>
            )}
            {entries.map((row) => (
              <article key={row.id} className={`companion-message ${row.role}`}>
                <div className="companion-message-meta">
                  <strong>
                    {row.role === "user" ? t("你") : desktop.name || t("伙伴")}
                  </strong>
                  {row.time && (
                    <time>
                      {new Date(row.time * 1000).toLocaleTimeString(locale(), {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </time>
                  )}
                </div>
                <div className="companion-markdown">
                  <ReactMarkdown
                    skipHtml
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ children }) => (
                        <span className="message-link">{children}</span>
                      ),
                      img: ({ alt }) => <span>{alt || t("图片")}</span>,
                    }}
                  >
                    {row.content}
                  </ReactMarkdown>
                </div>
              </article>
            ))}
            {runtime.context_enabled &&
              (runtime.context_text || runtime.context_status) && (
                <details className="companion-context">
                  <summary>
                    {t("桌面观察")} · {runtime.context_app}
                  </summary>
                  <p>{runtime.context_text || runtime.context_status}</p>
                </details>
              )}
            {runtime.notification_unread && runtime.notification_text && (
              <p className="companion-notice" role="status">
                {runtime.notification_text}
              </p>
            )}
          </div>
        </div>
        {away && (
          <button
            className="companion-latest"
            aria-label={t("回到最新消息")}
            onClick={() => {
              follow.current = true;
              scroll.current!.scrollTop = scroll.current!.scrollHeight;
              setAway(false);
            }}
          >
            ↓ {t("最新消息")}
          </button>
        )}
      </div>
      <TaskStatus
        runtime={runtime}
        busy={action.busy}
        act={action.act}
        compact
      />
      {desktop.feedback && (
        <p className="companion-feedback" role="status">
          <span>{t(desktop.feedback)}</span>
          <button
            className="text-button"
            aria-label={t("关闭提示")}
            onClick={() => bridge.desktopAction("dismiss-feedback")}
          >
            ×
          </button>
        </p>
      )}
      {action.status && !["操作已完成", "Done"].includes(action.status) && (
        <p className="companion-feedback" role="status">
          {action.status}
        </p>
      )}
      <form
        className="companion-composer"
        onSubmit={(event) => {
          event.preventDefault();
          run(submit());
        }}
      >
        <textarea
          aria-label={t("对话消息")}
          rows={2}
          value={text}
          onChange={(event) => {
            liveDraft.current = event.target.value;
            drafts.current[session.current] = event.target.value;
            setText(event.target.value);
          }}
          placeholder={t("和 {0} 说点什么…", desktop.name || t("伙伴"))}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              run(submit());
            }
          }}
        />
        <button
          className="button primary"
          aria-label={t("发送")}
          disabled={action.busy || !online || !text.trim()}
        >
          ↑
        </button>
      </form>
      <footer className="companion-footer">
        <button
          className={`companion-voice ${runtime.voice === "ready" ? "active" : ""}`}
          aria-pressed={runtime.voice === "ready"}
          disabled={
            !online ||
            action.busy ||
            ["connecting", "stopping"].includes(runtime.voice || "")
          }
          onClick={() => run(action.act("toggle_voice"))}
        >
          <svg
            width="13"
            height="16"
            viewBox="0 0 16 20"
            fill="none"
            aria-hidden="true"
          >
            <rect
              x="5"
              y="1"
              width="6"
              height="11"
              rx="3"
              stroke="currentColor"
              strokeWidth="1.6"
            />
            <path
              d="M2 9v1a6 6 0 0012 0V9M8 16v3M5 19h6"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
            />
          </svg>
          {runtime.voice === "ready"
            ? t("结束语音")
            : runtime.voice === "connecting"
              ? t("连接中…")
              : runtime.voice === "stopping"
                ? t("关闭中…")
                : t("开启语音")}
          <i className="voice-switch" />
        </button>
        <span className="companion-shortcut" title={t("全双工")}>
          ⌃⌥{t("空格")}
        </span>
        <button
          className="companion-session-button"
          aria-expanded={creating}
          aria-controls={creating ? "companion-new-session" : undefined}
          onClick={() => setCreating((value) => !value)}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            <path
              d="M6 2v8M2 6h8"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
          {t("新会话")}
        </button>
      </footer>
      {creating && (
        <form
          id="companion-new-session"
          className="companion-new-session"
          onSubmit={(event) => {
            event.preventDefault();
            run(create());
          }}
        >
          <input
            aria-label={t("新会话名称")}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder={t("新会话名称")}
          />
          <button className="button primary" disabled={action.busy || !online}>
            {t("创建并开始")}
          </button>
        </form>
      )}
    </section>
  );
}
startPreferences();
createRoot(document.getElementById("app")!).render(<Companion />);
