"""Parse a provider's terminal result representation, before evidence validation."""

import json
from typing import Any


def parse_result_object(text: str) -> dict[str, Any]:
    """Accept strict JSON or a complete terminal object after provider prose."""
    if not isinstance(text, str):
        raise TypeError("task result must be text")
    try:
        value = json.loads(text.strip())
    except ValueError:
        value = None
    if isinstance(value, dict):
        return value

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for offset, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text, offset)
        except ValueError:
            continue
        if isinstance(value, dict) and {"outcome", "summary", "evidence_steps"}.issubset(value):
            candidates.append(value)
    if candidates:
        return candidates[-1]
    raise ValueError("no terminal result object")
