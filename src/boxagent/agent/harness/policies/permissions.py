"""Permission decisions owned by the Harness rather than a Runtime backend."""

from collections.abc import Mapping


def can_auto_approve_computer_use(params: Mapping[str, object], *, stopped: bool = False) -> bool:
    """Only approve an action-only request from BoxAgent's own CU server."""
    schema = params.get("requestedSchema")
    return (
        not stopped
        and params.get("serverName") == "boxagent_cua"
        and isinstance(schema, Mapping)
        and schema.get("properties") == {}
    )
