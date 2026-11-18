"""Snapshot, decrypt, and WAL-replay WeChat SQLCipher databases.

Input is one account's ``db_storage`` tree plus the derived-key report.  Output
is plaintext SQLite under a private directory.  Every step is read-only with
respect to the running client: the client's files are copied, never opened for
writing, and all decrypted material stays under ``.runtime/private``.
"""

from __future__ import annotations

import ctypes as c
from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import time

PAGE_SIZE = 4096
SALT_SIZE = 16
RESERVE_SIZE = 80
IV_SIZE = 16
HMAC_SIZE = 64
SQLITE_MAGIC = b"SQLite format 3\x00"
WAL_HEADER_SIZE = 32
WAL_FRAME_HEADER_SIZE = 24




class SnapshotError(RuntimeError):
    """A snapshot, decryption, or consistency check failed."""


@dataclass(frozen=True)
class WalFrame:
    page_number: int
    db_size: int
    page: bytes


@lru_cache(maxsize=1)
def _crypto() -> c.CDLL:
    library = c.CDLL("/usr/lib/libSystem.B.dylib")
    library.CCCrypt.argtypes = [
        c.c_int32, c.c_int32, c.c_int32, c.c_char_p, c.c_size_t,
        c.c_char_p, c.c_char_p, c.c_size_t, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t),
    ]
    library.CCCrypt.restype = c.c_int32
    return library
_KCC_DECRYPT = 1
_KCC_ALGORITHM_AES = 0
_KCC_OPTION_ECB = 2


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC decryption without padding, using the system CommonCrypto."""
    if len(key) != 32 or len(iv) != 16 or len(data) % 16:
        raise SnapshotError("Invalid AES-CBC input sizes")
    out = c.create_string_buffer(len(data) + 16)
    moved = c.c_size_t()
    status = _crypto().CCCrypt(
        _KCC_DECRYPT, _KCC_ALGORITHM_AES, 0,
        key, len(key), iv, data, len(data), out, len(out), c.byref(moved),
    )
    if status != 0 or moved.value != len(data):
        raise SnapshotError(f"CCCrypt failed with status {status}")
    return out.raw[: len(data)]


def decrypt_page(key: bytes, page: bytes, page_number: int) -> bytes:
    """Decrypt one 4096-byte SQLCipher page (16-byte salt, 80-byte reserve)."""
    if len(page) != PAGE_SIZE:
        raise SnapshotError(f"Page {page_number} is not {PAGE_SIZE} bytes")
    iv = page[PAGE_SIZE - RESERVE_SIZE: PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
    if page_number == 1:
        encrypted = page[SALT_SIZE: PAGE_SIZE - RESERVE_SIZE]
        return SQLITE_MAGIC + aes_cbc_decrypt(key, iv, encrypted) + bytes(RESERVE_SIZE)
    encrypted = page[: PAGE_SIZE - RESERVE_SIZE]
    return aes_cbc_decrypt(key, iv, encrypted) + bytes(RESERVE_SIZE)


def wal_checksum(data: bytes, s1: int, s2: int, *, big_endian: bool) -> tuple[int, int]:
    """SQLite WAL checksum over 8-byte words, continuing the running state."""
    if len(data) % 8:
        raise SnapshotError("WAL checksum input must be 8-byte aligned")
    fmt = ">II" if big_endian else "<II"
    for offset in range(0, len(data), 8):
        first, second = struct.unpack(fmt, data[offset:offset + 8])
        s1 = (s1 + first + s2) & 0xFFFFFFFF
        s2 = (s2 + second + s1) & 0xFFFFFFFF
    return s1, s2


def read_wal_committed_frames(wal: bytes) -> list[WalFrame]:
    """Return the frames up to the last commit whose checksum chain is valid."""
    if len(wal) < WAL_HEADER_SIZE:
        return []
    magic = struct.unpack(">I", wal[0:4])[0]
    if magic not in (0x377F0682, 0x377F0683):
        raise SnapshotError(f"Unrecognized WAL magic {magic:#x}")
    big_endian = magic == 0x377F0683
    _version, page_size, _sequence, salt1, salt2 = struct.unpack(">IIIII", wal[4:24])
    if page_size != PAGE_SIZE:
        raise SnapshotError(f"Unexpected WAL page size {page_size}")
    stored1, stored2 = struct.unpack(">II", wal[24:32])
    s1, s2 = wal_checksum(wal[0:24], 0, 0, big_endian=big_endian)
    if (s1, s2) != (stored1, stored2):
        raise SnapshotError("WAL header checksum mismatch")

    frames: list[WalFrame] = []
    committed: list[WalFrame] = []
    offset = WAL_HEADER_SIZE
    while offset + WAL_FRAME_HEADER_SIZE + PAGE_SIZE <= len(wal):
        header = wal[offset:offset + WAL_FRAME_HEADER_SIZE]
        page_number, db_size, frame_salt1, frame_salt2, c1, c2 = struct.unpack(">IIIIII", header)
        page = wal[offset + WAL_FRAME_HEADER_SIZE:offset + WAL_FRAME_HEADER_SIZE + PAGE_SIZE]
        if (frame_salt1, frame_salt2) != (salt1, salt2):
            break
        s1, s2 = wal_checksum(header[0:8], s1, s2, big_endian=big_endian)
        s1, s2 = wal_checksum(page, s1, s2, big_endian=big_endian)
        if (s1, s2) != (c1, c2):
            break
        frame = WalFrame(page_number, db_size, page)
        frames.append(frame)
        if db_size:
            committed = list(frames)
        offset += WAL_FRAME_HEADER_SIZE + PAGE_SIZE
    return committed


def decrypt_database(db_path: Path, key: bytes, wal_path: Path | None) -> bytes:
    """Decrypt a database file and replay its committed WAL frames."""
    raw = db_path.read_bytes()
    if len(raw) % PAGE_SIZE:
        raise SnapshotError(f"{db_path.name} is not page aligned")
    pages = [
        decrypt_page(key, raw[offset:offset + PAGE_SIZE], offset // PAGE_SIZE + 1)
        for offset in range(0, len(raw), PAGE_SIZE)
    ]
    if wal_path is not None and wal_path.exists():
        for frame in read_wal_committed_frames(wal_path.read_bytes()):
            plain = decrypt_page(key, frame.page, frame.page_number)
            index = frame.page_number - 1
            if index >= len(pages):
                pages.extend([bytes(PAGE_SIZE)] * (index + 1 - len(pages)))
            pages[index] = plain
    return b"".join(pages)


def copy_snapshot(source: Path, target: Path) -> None:
    """Copy a database and its sidecars while the client keeps running."""
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        before = source.stat()
        shutil.copyfile(source, target)
        for suffix in ("-wal", "-shm"):
            sidecar = source.with_name(source.name + suffix)
            if sidecar.exists():
                shutil.copyfile(sidecar, target.with_name(target.name + suffix))
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return
        time.sleep(0.2)
    raise SnapshotError(f"{source.name} kept changing during copy")


def verify_plaintext(path: Path) -> tuple[bool, int]:
    """Open the decrypted database read-only and count its tables."""
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("pragma integrity_check").fetchone()
        count = connection.execute(
            "select count(*) from sqlite_master where type='table'",
        ).fetchone()[0]
        return result[0] == "ok", int(count)
    finally:
        connection.close()
