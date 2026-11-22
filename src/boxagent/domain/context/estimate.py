"""Deterministic, provider-independent token estimates for context accounting.

The estimate is a display heuristic, never a billing figure:

* one CJK ideograph, kana, hangul syllable or full-width punctuation mark
  counts as one token;
* every other character counts as a quarter token, rounded up per text.
"""

import json
import math


_WIDE_RANGES = (
    (0x3000, 0x303F),   # CJK symbols and punctuation
    (0x3040, 0x30FF),   # Hiragana and Katakana
    (0x3400, 0x4DBF),   # CJK Extension A
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0xAC00, 0xD7AF),   # Hangul syllables
    (0xF900, 0xFAFF),   # CJK compatibility ideographs
    (0xFF00, 0xFFEF),   # Half-width and full-width forms
)


def _is_wide(character: str) -> bool:
    point = ord(character)
    return any(start <= point <= end for start, end in _WIDE_RANGES)


def estimate_tokens(value) -> int:
    """Return the heuristic token count of a string or JSON-serializable value."""
    if value is None:
        return 0
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, separators=(",", ":"))
    wide = sum(1 for character in text if _is_wide(character))
    narrow = len(text) - wide
    return wide + math.ceil(narrow / 4)
