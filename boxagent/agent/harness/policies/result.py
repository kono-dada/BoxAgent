"""Harness policy for terminal results and their observable evidence."""

import json

from boxagent.core.errors import TaskFailure


RESULT_SCHEMA = {
    "type": "object", "properties": {
        "outcome": {"type": "string", "enum": ["completed", "blocked", "failed"]},
        "summary": {"type": "string"},
        "evidence_steps": {"type": "array", "items": {"type": "integer"}},
    }, "required": ["outcome", "summary", "evidence_steps"], "additionalProperties": False,
}


def _parse_result_object(text: str) -> dict:
    """Accept strict JSON and a final JSON object preceded by model prose.

    Some OpenAI-compatible providers do not enforce App Server outputSchema and
    may emit a short natural-language sentence before the requested object.  We
    still require a syntactically complete object with the terminal result keys;
    arbitrary prose or a partial object remains invalid.
    """
    if not isinstance(text, str):
        raise TypeError("task result must be text")
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value

    decoder = json.JSONDecoder()
    candidates = []
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text, offset)
        except ValueError:
            continue
        if isinstance(value, dict) and {
                "outcome", "summary", "evidence_steps"}.issubset(value):
            candidates.append(value)
    if candidates:
        return candidates[-1]
    raise ValueError("no terminal result object")


def validate_task_result(text: str, observations: dict[int, dict],
                         tool_errors: list[dict]) -> dict:
    try:
        result = _parse_result_object(text)
    except (ValueError, TypeError) as exc:
        raise TaskFailure("invalid_result", "执行结果无法解析，请检查目标应用后重试", text) from exc
    if not isinstance(result, dict):
        raise TaskFailure("invalid_result", "执行结果无法解析，请检查目标应用后重试", text)
    if result.get("outcome") not in {"completed", "blocked", "failed"} or not isinstance(result.get("summary"), str):
        raise TaskFailure("invalid_result", "执行已结束，但返回结果无法解析", text)
    steps = result.get("evidence_steps", [])
    if not isinstance(steps, list) or any(type(step) is not int or step not in observations for step in steps):
        raise TaskFailure("invalid_evidence", "无法确认任务是否完成，请检查目标应用后重试",
                          f"后台结果引用了不存在的观察记录：{steps}；实际步骤：{list(observations)}")
    if result["outcome"] == "completed" and not steps:
        raise TaskFailure("missing_evidence", "缺少完成依据，请检查目标应用后重试")
    if observations and not steps:
        raise TaskFailure("missing_result_evidence", "执行已结束，但未能确认最终结果。请检查目标应用。",
                          "执行过工具，但最终结论没有引用任何观察记录：" + text)
    if result["outcome"] == "completed" and not any(observations[step].get("success", True) for step in steps):
        raise TaskFailure("failed_evidence", "执行已结束，工具操作失败，无法确认完成")
    if tool_errors and not any(item.get("success", True) for item in observations.values()):
        if any("bootstrapTimedOut" in item["error"] for item in tool_errors):
            result.update(outcome="blocked",
                          summary="电脑操作服务连接超时，任务已结束。未能读取目标应用，请检查 Computer Use 服务后重试。")
    return {"outcome": result["outcome"], "summary": result["summary"],
            "evidence": [observations[step] for step in steps],
            "assessment": "agent", "steps": len(observations), "tool_errors": tool_errors}
