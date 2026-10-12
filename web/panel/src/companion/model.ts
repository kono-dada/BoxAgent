import type { ProductMessage, RuntimeState } from "../types";
export interface ChatEntry {
  id: string;
  role: string;
  content: string;
  time?: number;
}
export function transcript(
  history: ProductMessage[],
  state: RuntimeState,
  pending: string,
): ChatEntry[] {
  const interactions = [
    ...new Set(history.map((row) => row.interaction_id).filter(Boolean)),
  ].slice(-30);
  const visible = history.filter(
    (row) => !row.interaction_id || interactions.includes(row.interaction_id),
  );
  const rows: ChatEntry[] = visible
    .filter(
      (row) =>
        row.type === "message.final" &&
        ["user", "assistant"].includes(row.role || "") &&
        row.content?.trim(),
    )
    .map((row) => ({
      id: row.event_id || String(row.sequence),
      role: row.role!,
      content: row.content!,
      time: row.occurred_at,
    }));
  const lastUser =
    [...rows].reverse().find((row) => row.role === "user")?.content || "";
  const lastAssistant =
    [...rows].reverse().find((row) => row.role === "assistant")?.content || "";
  const user = pending || state.user_text;
  if (user && user !== lastUser)
    rows.push({ id: "live-user", role: "user", content: user });
  if (state.assistant_text && state.assistant_text !== lastAssistant)
    rows.push({
      id: "live-assistant",
      role: "assistant",
      content: state.assistant_text,
    });
  return rows;
}
export function taskTiming(state: RuntimeState, now: number) {
  const elapsed = state.task_started_at
    ? Math.max(
        0,
        Math.floor((state.task_ended_at || now) - state.task_started_at),
      )
    : 0;
  const quiet = state.task_activity_at
    ? Math.max(0, Math.floor(now - state.task_activity_at))
    : 0;
  return { elapsed, quiet };
}
