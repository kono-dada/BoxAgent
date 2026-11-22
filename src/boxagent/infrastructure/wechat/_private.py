"""Private local state helpers; no framework or experiment dependencies."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import cast


def object_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("Expected a JSON object")
    return cast("dict[str, object]", value)


def private_dir(path: Path) -> Path:
    if not path.parent.exists():
        private_dir(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Private directory ownership/type mismatch")
    if info.st_mode & 0o077:
        os.chmod(path, 0o700)
    return path


def write_json(path: Path, value: object) -> None:
    # Atomic replacement prevents a partial journal from being mistaken for a retry.
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
