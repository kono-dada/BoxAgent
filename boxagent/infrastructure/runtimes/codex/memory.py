"""Automatic memory candidate extraction through an isolated Codex turn."""

import json

from boxagent.domain.memory.models import MemoryCandidate
from boxagent.domain.memory.policy import redact_memory_source

MEMORY_CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "subject": {"type": "string", "enum": ["user", "assistant", "third_party"]},
                    "kind": {"type": "string", "enum": [
                        "profile", "preference", "relationship", "goal",
                        "commitment", "episode", "procedure"]},
                    "durability": {"type": "string", "enum": [
                        "temporary", "stable", "long_term"]},
                    "operation": {"type": "string", "enum": ["add", "update", "ignore"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "sensitivity": {"type": "string", "enum": [
                        "normal", "sensitive", "secret"]},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "event_id": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["event_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                    "expires_at": {"type": ["string", "null"]},
                    "canonical_slot": {
                        "type": "string",
                        "description": "稳定语义槽，格式 kind:name，例如 preference:music_genre；同一事实被更正时必须复用已有槽。",
                    },
                },
                "required": [
                    "content", "subject", "kind", "durability", "operation",
                    "confidence", "sensitivity", "evidence", "expires_at",
                    "canonical_slot"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

MEMORY_EXTRACTOR_INSTRUCTIONS = (
    "你是 BoxAgent 的长期记忆候选提取器。只从 target_messages 中用户直接陈述的内容提取候选；"
    "context_messages 只用于消解指代，不能成为证据。普通寒暄、一次性操作命令、临时情绪、"
    "助手猜测、工具中间状态、密码、验证码、Token 和 API Key 不得进入候选。"
    "只保留未来跨会话仍有帮助的用户事实、偏好、关系、长期目标、共同承诺、重要经历或稳定流程。"
    "每个候选必须原子、自包含，并引用 target_messages 中真实 user event_id 与原文片段。"
    "为每个候选给出稳定 canonical_slot，格式为 kind:name；若它更正 existing_memories 中同一语义槽，"
    "operation 使用 update 并复用该槽。"
    "不需要记忆时返回空 candidates。你只做提取，不写库、不调用工具。"
)


class CodexMemoryCandidateExtractor:
    version = "codex-memory-v1"

    def __init__(self, session_factory, *, model, provider="codex", log=None):
        self.session_factory = session_factory
        self.model = model
        self.provider = provider
        self.log = log or (lambda *_args, **_kwargs: None)

    async def extract(self, *, target_messages, context_messages,
                      existing_memories):
        target_ids = {item.event_id for item in target_messages if item.role == "user"}
        if not target_ids:
            return ()

        async def reject_tool(name, arguments):
            del name, arguments
            raise RuntimeError("Memory Extractor 不允许调用工具")

        session = self.session_factory(
            output=None, on_tool_call=reject_tool,
            approve=lambda _params: False, current_app=lambda: "",
            report=lambda _phase, _message: None, log=self.log,
            auto_approve=False)
        try:
            await session.start()
            payload = {
                "target_messages": [self._message_payload(item) for item in target_messages],
                "context_messages": [self._message_payload(item) for item in context_messages],
                "existing_memories": [
                    {"memory_id": item.memory_id, "content": item.content,
                     "kind": item.kind, "canonical_slot": item.canonical_slot}
                    for item in existing_memories[:20]
                ],
            }
            text = await session.run_isolated_structured_turn(
                model=self.model,
                instructions=MEMORY_EXTRACTOR_INSTRUCTIONS,
                query=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                output_schema=MEMORY_CANDIDATE_SCHEMA)
            value = json.loads(text)
            candidates = tuple(MemoryCandidate.from_payload(item)
                               for item in value.get("candidates", []))
            for candidate in candidates:
                if any(item.get("event_id") not in target_ids
                       for item in candidate.evidence):
                    raise ValueError("Memory Candidate 引用了 target 之外的证据")
            return candidates
        finally:
            await session.close()

    @staticmethod
    def _message_payload(item):
        return {"event_id": item.event_id, "role": item.role,
                "content": redact_memory_source(item.content)}
