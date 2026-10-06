"""Explicit-memory admission and deletion guardrails."""

import hashlib
import re

from boxagent.domain.memory.models import MemoryCandidate

MEMORY_TEXT_LIMIT = 8000
MEMORY_QUERY_LIMIT = 2000

MEMORY_KINDS = {
    "profile", "preference", "relationship", "goal", "commitment",
    "episode", "procedure",
}

SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9_.-]{1,95}$")

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_.-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bapikey_[A-Za-z0-9_.-]{12,}\b", re.IGNORECASE),
    re.compile(r"(?i)(password|passwd|密码|验证码|token|api[_ -]?key)\s*[:=：]\s*\S+"),
)


def require_text(value, *, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空文本")
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"{name} 不能超过 {limit} 个字符")
    return value


def require_memory_ids(memory_ids) -> list[str]:
    if (not isinstance(memory_ids, list) or not memory_ids
            or any(not isinstance(item, str) or not item.strip() for item in memory_ids)):
        raise ValueError("memory_ids 必须是非空记忆 ID 列表")
    return [item.strip() for item in memory_ids]


def redact_memory_source(value: str) -> str:
    text = str(value or "")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[敏感信息已删除]", text)
    return text


def contains_secret(value: str) -> bool:
    return any(pattern.search(str(value or "")) for pattern in SECRET_PATTERNS)


def memory_admission(candidate: MemoryCandidate) -> str:
    """Return active, pending_review, or rejected without trusting the model."""
    if not candidate.content.strip() or contains_secret(candidate.content):
        return "rejected"
    if candidate.subject != "user" or candidate.kind not in MEMORY_KINDS:
        return "rejected"
    if candidate.operation not in {"add", "update"}:
        return "pending_review"
    if candidate.sensitivity != "normal":
        return "pending_review"
    if candidate.durability not in {"stable", "long_term"}:
        return "rejected"
    if candidate.confidence < 0.8 or not candidate.evidence:
        return "pending_review"
    return "active"


def canonical_slot(candidate: MemoryCandidate) -> str:
    """Return a stable semantic slot without trusting arbitrary model output."""
    supplied = str(candidate.canonical_slot or "").strip().casefold()
    if supplied and SLOT_PATTERN.fullmatch(supplied):
        return supplied
    digest = hashlib.sha256(
        "".join(candidate.content.casefold().split()).encode("utf-8")
    ).hexdigest()[:20]
    return f"{candidate.kind}:{digest}"
