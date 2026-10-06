"""Portable Context Checkpoint generation through an isolated Codex turn."""

import json

from boxagent.domain.memory.policy import redact_memory_source


CHECKPOINT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "user_facts": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "open_loops": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "user_facts", "decisions", "open_loops"],
    "additionalProperties": False,
}

CHECKPOINT_INSTRUCTIONS = (
    "你是 BoxAgent 的上下文压缩器。只根据输入 JSON 生成可恢复后续对话的结构化 Checkpoint。"
    "保留用户明确事实与偏好、双方已确认的决策、尚未完成的事项，以及理解后续代词所需的背景。"
    "不要把助手猜测写成用户事实，不要引入输入中没有的信息，不要执行工具或外部操作。"
    "previous_checkpoint 是更早历史的已有压缩结果；messages 是其后的原生对话。"
    "输出必须严格符合给定 JSON Schema。"
)


class CodexCheckpointGenerator:
    provider = "codex"

    def __init__(self, session_factory, *, model, provider=None, log=None):
        self.session_factory = session_factory
        self.model = model
        self.provider = provider or self.provider
        self.log = log or (lambda *_args, **_kwargs: None)

    async def generate(self, *, previous, messages):
        if not messages:
            raise ValueError("没有可压缩的会话消息")

        async def reject_tool(name, arguments):
            del name, arguments
            raise RuntimeError("Context Checkpoint 不允许调用工具")

        session = self.session_factory(
            output=None,
            on_tool_call=reject_tool,
            approve=lambda _params: False,
            current_app=lambda: "",
            report=lambda _phase, _message: None,
            log=self.log,
            auto_approve=False,
        )
        try:
            await session.start()
            payload = {
                "previous_checkpoint": previous.content if previous else None,
                "messages": [
                    {"sequence": item.sequence, "role": item.role,
                     "content": redact_memory_source(item.content)}
                    for item in messages
                ],
            }
            text = await session.run_isolated_structured_turn(
                model=self.model,
                instructions=CHECKPOINT_INSTRUCTIONS,
                query=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                output_schema=CHECKPOINT_SCHEMA,
            )
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError("Codex Checkpoint 输出必须是 JSON object")
            return value
        finally:
            await session.close()
