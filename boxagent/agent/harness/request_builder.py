"""Compile product input into the stable contract consumed by a Runtime."""

from boxagent.agent.runtime.models import RuntimeRequest
from boxagent.agent.harness.context import RuntimeContextProjector
from boxagent.agent.harness.request import HarnessInput
from boxagent.agent.harness.persona import Persona
from boxagent.agent.harness.instructions import compile_instructions
from boxagent.agent.harness.policies.result import RESULT_SCHEMA


class RuntimeRequestBuilder:
    def __init__(self, *, persona: Persona | None = None,
                 context_assembler: RuntimeContextProjector | None = None):
        self.persona = persona
        self.context_assembler = context_assembler or RuntimeContextProjector()
        self.instructions = compile_instructions(persona)

    def prepare(self, value: str | HarnessInput) -> RuntimeRequest:
        item = value if isinstance(value, HarnessInput) else HarnessInput(goal=value)
        if not isinstance(item.goal, str) or not item.goal.strip():
            raise ValueError("请提供要完成的目标")
        return RuntimeRequest(
            query=item.goal,
            developer_instructions=self.instructions,
            output_schema=RESULT_SCHEMA,
            persona_source=str(self.persona.source) if self.persona else None,
            history=self.context_assembler.runtime_messages(turns=item.turns),
            history_delta=self.context_assembler.runtime_messages(
                turns=item.turns, after_sequence=item.context_cursor,
                require_user_start=False),
            evidence_context=self.context_assembler.evidence_packet(
                memories=item.memories, environment=item.environment),
        )
