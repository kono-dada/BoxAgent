"""Permission decisions owned by the Harness rather than a Runtime backend."""


def can_auto_approve_computer_use(params: dict, *, stopped: bool = False) -> bool:
    """Only approve an action-only request from BoxAgent's own CU server."""
    return (
        not stopped
        and params.get("serverName") == "boxagent_cua"
        and params.get("requestedSchema", {}).get("properties") == {}
    )
