"""Independent, offline candidate parsing and SQLCipher first-page authentication.

No process attachment, account discovery, key persistence, or database writes.
The supported profile is the 4096-byte / HMAC-SHA512 profile observed in the
local wechat-use executable, not a promise about every WeChat release.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import hmac
import re
from typing import Literal


PAGE_SIZE = 4096
_PATTERN = re.compile(rb"x'([0-9a-fA-F]{96})'")
_OVERLAP = 98
AuthenticationVariant = Literal["derived-le", "derived-be", "raw-le"]


class CandidateLimitExceeded(ValueError):
    """The caller's scan budget was exhausted; do not use partial results."""


class AmbiguousKeyError(ValueError):
    """More than one distinct candidate authenticated the page."""


@dataclass(frozen=True)
class KeyCandidate:
    key: bytes = field(repr=False)
    salt: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.key) != 32 or len(self.salt) != 16:
            raise ValueError("Expected a 32-byte key and a 16-byte salt")


@dataclass(frozen=True)
class AuthenticatedCandidate:
    candidate: KeyCandidate = field(repr=False)
    variant: AuthenticationVariant


def parse_candidates(
    chunks: Iterable[bytes], *, max_bytes: int = 16 * 1024 * 1024,
    max_candidates: int = 256,
) -> tuple[KeyCandidate, ...]:
    """Parse bounded, already supplied bytes; preserve literals across chunks.

    Adjacent chunks must describe contiguous bytes from the same source.
    Defaults are local PoC budgets, not recovered upstream defaults.
    """
    if max_bytes <= 0 or max_candidates <= 0:
        raise ValueError("Scan budgets must be positive")
    tail = b""
    total = 0
    found: dict[KeyCandidate, None] = {}
    for chunk in chunks:
        total += len(chunk)
        if total > max_bytes:
            raise CandidateLimitExceeded("Byte budget exhausted")
        buffer = tail + chunk
        for match in _PATTERN.finditer(buffer):
            decoded = bytes.fromhex(match.group(1).decode("ascii"))
            candidate = KeyCandidate(decoded[:32], decoded[32:])
            if candidate not in found:
                if len(found) >= max_candidates:
                    raise CandidateLimitExceeded("Candidate budget exhausted")
                found[candidate] = None
        tail = buffer[-_OVERLAP:]
    return tuple(found)


def authenticate_first_page(
    page: bytes, candidate: KeyCandidate,
) -> AuthenticationVariant | None:
    """Authenticate ciphertext; a successful result is not message decryption."""
    if len(page) != PAGE_SIZE:
        raise ValueError("Only an exact 4096-byte first page is supported")
    if page.startswith(b"SQLite format 3\x00"):
        raise ValueError("Expected an encrypted page, received plain SQLite")
    if not hmac.compare_digest(page[:16], candidate.salt):
        return None
    mac_salt = bytes(value ^ 0x3A for value in candidate.salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", candidate.key, mac_salt, 2, dklen=32)
    # SQLCipher reserve: 16-byte IV + 64-byte HMAC. Salt is excluded;
    # ciphertext and IV are included, followed by the four-byte page number.
    variants: tuple[tuple[AuthenticationVariant, bytes, bytes], ...] = (
        ("derived-le", mac_key, b"\x01\x00\x00\x00"),
        ("derived-be", mac_key, b"\x00\x00\x00\x01"),
        ("raw-le", candidate.key, b"\x01\x00\x00\x00"),
    )
    for name, key, page_number in variants:
        digest = hmac.digest(key, page[16:-64] + page_number, "sha512")
        if hmac.compare_digest(digest, page[-64:]):
            return name
    return None


def select_unique_candidate(
    page: bytes, candidates: Iterable[KeyCandidate], *, max_candidates: int = 256,
) -> AuthenticatedCandidate | None:
    """Return one authenticated key or fail when the result is ambiguous."""
    if max_candidates <= 0:
        raise ValueError("Candidate budget must be positive")
    selected: AuthenticatedCandidate | None = None
    seen: set[KeyCandidate] = set()
    for index, candidate in enumerate(candidates):
        if index >= max_candidates:
            raise CandidateLimitExceeded("Validation budget exhausted")
        if candidate in seen:
            continue
        seen.add(candidate)
        variant = authenticate_first_page(page, candidate)
        if variant is None:
            continue
        if selected is not None:
            raise AmbiguousKeyError("Multiple distinct candidates authenticated")
        selected = AuthenticatedCandidate(candidate, variant)
    return selected
