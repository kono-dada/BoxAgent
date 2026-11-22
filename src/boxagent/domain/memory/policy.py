"""Memory privacy and input guardrails owned by BoxAgent."""

import re

MEMORY_TEXT_LIMIT = 8000
MEMORY_QUERY_LIMIT = 2000

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
