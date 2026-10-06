"""Pure projection from engine state to native view text."""

import time


VOICE_MODES = {"off": "", "connecting": "连接语音中", "ready": "聆听中",
               "stopping": "关闭语音中"}
TASK_MODES = {"accepted": "准备中", "running": "执行中",
              "awaiting_approval": "等待授权", "cancelling": "停止中",
              "succeeded": "已结束 · 完成", "failed": "已结束 · 未完成",
              "blocked": "已结束 · 受阻", "cancelled": "已停止"}
ACTIVE_TASK_STATES = {"accepted", "running", "awaiting_approval", "cancelling"}


def mode_text(state) -> str:
    voice = "回复中" if state.speaking else VOICE_MODES.get(state.voice, "")
    return " · ".join(filter(None, [TASK_MODES.get(state.task, ""), voice]))


def transcript_sections(state, input_feedback="", *, now=None):
    now = time.time() if now is None else now
    sections = []
    if state.context_text:
        stamp = time.strftime("%H:%M:%S", time.localtime(state.context_at))
        sections.append((f"桌面观察 · {state.context_app} · {stamp}", state.context_text))
    elif state.context_status:
        sections.append(("桌面观察", state.context_status))
    if state.user_text:
        sections.append(("你", state.user_text))
    if state.assistant_text:
        sections.append(("BoxAgent", state.assistant_text))
    if state.approval:
        sections.append(("需要授权", state.approval))
    elif state.error:
        sections.append(("提示", state.error))
    elif state.task_text and state.task_text != state.assistant_text:
        sections.append(("任务", state.task_text))
    if state.task_started_at:
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
