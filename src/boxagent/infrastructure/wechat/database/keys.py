"""Load and authenticate existing local material; never captures new keys."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from boxagent.infrastructure.wechat._private import object_json
from boxagent.infrastructure.wechat.database._authentication import (
    KeyCandidate,
    authenticate_first_page,
)


def read_first_page(path: Path) -> bytes | None:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        data = os.pread(fd, 4096, 0)
    finally:
        os.close(fd)
    return data if len(data) == 4096 and not data.startswith(b"SQLite format 3\0") else None


class DatabaseKeys:
    def __init__(self, root: Path, report: Path | None = None, *, self_wxid: str | None = None,
                 material: Mapping[str, str] | None = None) -> None:
        self.root = root.expanduser().resolve(strict=True)
        match = re.fullmatch(r"(wxid_.+)_[0-9a-f]{4}", self.root.parent.name)
        if self_wxid is None and match is None:
            raise ValueError("Cannot identify the account; provide self_wxid")
        self.self_wxid = self_wxid or str(match[1] if match else "")
        self.account_id = "wechat:" + self.self_wxid
        values = (
            material
            if material is not None
            else object_json(report).get("keys")
            if report is not None
            else None
        )
        if not isinstance(values, Mapping):
            raise ValueError("Existing key report lacks its keys mapping")
        self._keys: dict[str, bytes] = {}
        for relative, value in values.items():
            if not isinstance(relative, str) or not isinstance(value, str):
                raise ValueError("Invalid key report shape")
            raw = bytes.fromhex(value)
            if len(raw) != 32:
                raise ValueError("Invalid existing key dimensions")
            self._keys[relative] = raw

    def path(self, relative: str) -> Path:
        candidate = self.root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Database path escapes the selected account")
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or resolved != candidate:
            raise ValueError("Database path aliases another location")
        return resolved

    def key(self, relative: str) -> bytes:
        raw = self._keys.get(relative)
        if raw is None:
            raise ValueError("No existing key for the requested database")
        page = read_first_page(self.path(relative))
        if (
            page is None
            or authenticate_first_page(page, KeyCandidate(raw, page[:16])) != "derived-le"
        ):
            raise ValueError("Existing material does not authenticate this account/database")
        return raw

    def messages(self) -> tuple[str, ...]:
        names = tuple(
            sorted(
                str(p.relative_to(self.root))
                for p in (self.root / "message").glob("message_*.db")
                if re.fullmatch(r"message_[0-9]+\.db", p.name)
            )
        )
        if not names:
            raise ValueError("No message shards found")
        return names
