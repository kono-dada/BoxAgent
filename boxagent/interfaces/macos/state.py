"""Pure projection from engine state to native view text."""

import time


VOICE_MODES = {"off": "", "connecting": "连接语音中", "ready": "聆听中",
               "stopping": "关闭语音中"}
TASK_MODES = {"accepted": "准备中", "running": "执行中",
              "awaiting_approval": "等待授权", "cancelling": "停止中",
              "succeeded": "已结束 · 完成", "failed": "已结束 · 未完成",
              "blocked": "已结束 · 受阻", "cancelled": "已停止"}
ACTIVE_TASK_STATES = {"accepted", "running", "awaiting_approval", "cancelling"}
DEFAULT_VISIBLE_TURNS = 30


def mode_text(state) -> str:
    voice = "回复中" if state.speaking else VOICE_MODES.get(state.voice, "")
    return " · ".join(filter(None, [TASK_MODES.get(state.task, ""), voice]))


def recent_turn_events(history, turn_limit=DEFAULT_VISIBLE_TURNS):
    history = tuple(history or ())
    interaction_ids = []
    for event in history:
        interaction_id = event.get("interaction_id")
        if interaction_id and interaction_id not in interaction_ids:
            interaction_ids.append(interaction_id)
    visible = set(interaction_ids[-turn_limit:])
    return tuple(event for event in history
                 if not event.get("interaction_id")
                 or event.get("interaction_id") in visible)


def transcript_sections(state, input_feedback="", *, history=(),
                        assistant_name="伙伴", pending_user_text="", now=None):
    now = time.time() if now is None else now
    sections = []
    if state.context_text:
        stamp = time.strftime("%H:%M:%S", time.localtime(state.context_at))
        sections.append((f"桌面观察 · {state.context_app} · {stamp}", state.context_text))
    elif state.context_status:
        sections.append(("桌面观察", state.context_status))

    history = recent_turn_events(history)
    final_messages = []
    interaction_started_at = {}
    terminal_task_ids = set()
    for event in history:
        event_type = event.get("type")
        interaction_id = event.get("interaction_id")
        if event_type == "interaction.started" and interaction_id:
            interaction_started_at[interaction_id] = event.get("occurred_at") or 0
        elif event_type == "message.final" and event.get("role") in {"user", "assistant"}:
            content = str(event.get("content") or "").strip()
            if content:
                final_messages.append(event)
                sections.append(("你" if event["role"] == "user" else assistant_name,
                                 content))
        elif event_type == "interaction.finalized" and event.get("task_id"):
            terminal_task_ids.add(event["task_id"])
            status = event.get("status")
            status_text = {
                "succeeded": "执行已结束",
                "failed": "执行未完成",
                "blocked": "执行受阻",
                "cancelled": "执行已停止",
                "interrupted": "执行已中断",
            }.get(status, "执行已结束")
            started_at = interaction_started_at.get(interaction_id)
            ended_at = event.get("occurred_at") or 0
            if started_at and ended_at >= started_at:
                status_text += f" · 用时 {max(0, int(ended_at - started_at))} 秒"
            sections.append(("状态", status_text))

    last_user = next((str(item.get("content") or "").strip()
                      for item in reversed(final_messages)
                      if item.get("role") == "user"), "")
    last_assistant = next((str(item.get("content") or "").strip()
                           for item in reversed(final_messages)
                           if item.get("role") == "assistant"), "")
    visible_user_text = pending_user_text or state.user_text
    if visible_user_text and visible_user_text != last_user:
        sections.append(("你", visible_user_text))
    if state.assistant_text and state.assistant_text != last_assistant:
        sections.append((assistant_name, state.assistant_text))
    if state.approval:
        sections.append(("需要授权", state.approval))
    elif state.error:
        sections.append(("提示", state.error))
    elif (state.task in ACTIVE_TASK_STATES and state.task_text
          and state.task_text != state.assistant_text):
        sections.append(("任务", state.task_text))
    task_already_in_history = bool(
        state.task_id and state.task_id in terminal_task_ids)
    if state.task_started_at and (state.task in ACTIVE_TASK_STATES
                                  or not task_already_in_history):
        end = state.task_ended_at or now
        elapsed = max(0, int(end - state.task_started_at))
        if state.task_ended_at:
            sections.append(("状态", f"执行已结束 · 用时 {elapsed} 秒"))
        else:
            quiet = max(0, int(now - state.task_activity_at))
            status = f"已运行 {elapsed} 秒"
            if quiet >= 15:
                status += f" · {quiet} 秒未收到新进展，可停止任务"
            sections.append(("状态", status))
    if input_feedback:
        sections.append(("提示", input_feedback))
    return sections
