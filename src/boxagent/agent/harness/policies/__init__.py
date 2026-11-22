"""Policies applied by the BoxAgent Harness."""

from boxagent.agent.harness.policies.permissions import can_auto_approve_computer_use
from boxagent.agent.harness.policies.result import RESULT_SCHEMA, validate_task_result
from boxagent.agent.harness.policies.tools import ComputerUseToolGateway

__all__ = [
    "ComputerUseToolGateway",
    "RESULT_SCHEMA",
    "can_auto_approve_computer_use",
    "validate_task_result",
]
