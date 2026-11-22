"""Nonblocking exclusive file leases on Windows and POSIX."""

import os
from typing import TextIO

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(stream: TextIO, operation: int) -> None:
    if os.name != "nt":
        import fcntl
        fcntl.flock(stream, operation)
        return
    import msvcrt

    position = stream.tell()
    try:
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(),
                           msvcrt.LK_UNLCK if operation == LOCK_UN else msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if operation != LOCK_UN:
                raise BlockingIOError("File lease is already held") from exc
            raise
    finally:
        stream.seek(position)
