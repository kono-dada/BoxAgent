"""Codex Agent Runtime backed by the native App Server turn API."""


class CodexAgentRuntime:
    provider = "codex"

    def __init__(self, session, *, model: str):
        self.session = session
        self.model = model

    async def execute(self, request, tools, call_tool, validate_result, progress):
        del call_tool, validate_result
        progress("正在请 Codex 规划")
        self.session.tools = tools
        return await self.session.run_codex_turn(
            model=self.model,
            instructions=request.developer_instructions,
            query=request.query,
            history=request.history,
            history_delta=request.history_delta,
            evidence_context=request.evidence_context,
            output_schema=request.output_schema,
        )
