"""Stable identifier creation."""

import uuid


def new_task_id() -> str:
    return uuid.uuid4().hex[:12]


def new_session_id() -> str:
    return "ses_" + uuid.uuid4().hex[:16]


def new_interaction_id() -> str:
    return "int_" + uuid.uuid4().hex[:16]


def new_event_id() -> str:
    return "evt_" + uuid.uuid4().hex[:16]


def new_notification_id() -> str:
    return "ntf_" + uuid.uuid4().hex[:16]


def new_checkpoint_id() -> str:
    return "ckp_" + uuid.uuid4().hex[:16]


def new_segment_id() -> str:
    return "seg_" + uuid.uuid4().hex[:16]


def new_memory_id() -> str:
    return "mem_" + uuid.uuid4().hex[:16]


def new_memory_job_id() -> str:
    return "mjob_" + uuid.uuid4().hex[:16]


def new_skill_draft_id() -> str:
    return "skd_" + uuid.uuid4().hex[:16]
