"""Codex Agent Runtime backed by the native App Server turn API."""

import json

from boxagent.core.errors import TaskFailure
from boxagent.agent.runtime.contracts import Progress, ResultValidator, ToolCall
from boxagent.agent.runtime.models import RuntimeRequest


class CodexAgentRuntime:
    provider = "codex"

    def __init__(self, session, *, model: str):
        self.session = session
        self.model = model

    async def execute(self, request: RuntimeRequest, tools: list[dict], call_tool: ToolCall,
                      validate_result: ResultValidator, progress: Progress) -> str:
        del call_tool
        progress("正在请 Codex 规划")
        self.session.tools = tools
        text = await self.session.run_codex_turn(
            model=self.model,
            instructions=request.developer_instructions,
            query=request.query,
            history=request.history,
            history_delta=request.history_delta,
            evidence_context=request.evidence_context,
            output_schema=request.output_schema,
        )
        try:
            validate_result(text)
        except TaskFailure as exc:
            if exc.code != "invalid_result":
                raise
            # A provider can ignore outputSchema and ask a valid clarification
            # in prose. Repair its representation once, without replaying tools.
            progress("正在整理任务结果")
            text = await self.session.run_isolated_structured_turn(
                model=self.model,
                instructions=(
                    "将原始任务答复转换成要求的 JSON，只整理格式，不执行任务、不调用工具。"
                    "仅使用给出的答复，不添加事实或编造步骤。"
                    "需要用户澄清、登录或补充信息时 outcome=blocked，summary 保留具体问题。"
                    "没有实际完成证据时不得标为 completed。"
                    "evidence_steps 仅可引用原答复明确提供的观察编号，没有编号就用空数组。"),
                query=json.dumps({"user_goal": request.query, "original_reply": text}, ensure_ascii=False),
                output_schema=request.output_schema,
            )
            validate_result(text)
        return text
