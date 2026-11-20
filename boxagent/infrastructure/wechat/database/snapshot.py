"""Validated private snapshots; reuses the verified page decrypter and WAL replay."""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from boxagent.infrastructure.wechat.database._authentication import KeyCandidate, authenticate_first_page
from boxagent.infrastructure.wechat.database._cipher import SnapshotError, copy_snapshot, decrypt_database
from boxagent.infrastructure.wechat.database.keys import DatabaseKeys, read_first_page
from boxagent.infrastructure.wechat._private import private_dir

@dataclass(frozen=True)
class FileStamp:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


def stamp(path: Path) -> FileStamp | None:
    try:
        value = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    if path.is_symlink() or not path.is_file() or value.st_uid != os.getuid():
        raise ValueError("Unexpected source ownership/type")
    return FileStamp(value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def signature(path: Path) -> tuple[FileStamp | None, FileStamp | None]:
    return stamp(path), stamp(path.with_name(path.name + "-wal"))

@dataclass(frozen=True)
class RefreshCost:
    database: str
    reason: str
    copy_ms: float
    decrypt_ms: float
    validate_ms: float
    cpu_ms: float
    wall_ms: float
    copied_bytes: int
    plaintext_bytes: int
    attempts: int


@dataclass
class SnapshotDatabase:
    name: str
    path: Path
    key: bytes = field(repr=False)
    run_dir: Path
    generation: str = ""
    last_signature: tuple[FileStamp | None, FileStamp | None] | None = None

    def refresh(self, reason: str) -> tuple[Path, RefreshCost, tuple[FileStamp | None, FileStamp | None]]:
        started, cpu = time.perf_counter(), time.process_time()
        copied = 0
        copy_ms = 0.0
        with tempfile.TemporaryDirectory(prefix="snapshot-", dir=self.run_dir) as directory:
            encrypted = Path(directory) / "encrypted.db"
            stable: tuple[FileStamp | None, FileStamp | None] | None = None
            for attempt in range(1, 4):
                before = signature(self.path)
                if before[0] is None:
                    raise SnapshotError("Source database disappeared")
                # Remove stale sidecars when a WAL disappears between retries.
                for suffix in ("", "-wal", "-shm"):
                    target = encrypted.with_name(encrypted.name + suffix)
                    target.unlink(missing_ok=True)
                    target.touch(mode=0o600)
                tick = time.perf_counter()
                try:
                    copy_snapshot(self.path, encrypted)
                except FileNotFoundError:
                    # Concurrent checkpoint: bounded retry, never advance cursors.
                    continue
                finally:
                    copy_ms += (time.perf_counter()-tick)*1000
                    copied += sum(p.stat().st_size for p in Path(directory).iterdir() if p.is_file())
                after = signature(self.path)
                if before == after:
                    stable = after
                    break
                time.sleep(0.05)
            if stable is None or stable[0] is None:
                raise SnapshotError("Database/WAL changed during all snapshot attempts")
            page = read_first_page(encrypted)
            if page is None or authenticate_first_page(page, KeyCandidate(self.key, page[:16])) != "derived-le":
                raise SnapshotError("Existing key does not authenticate this account/database")
            generation = hashlib.sha256(
                str(self.path).encode() + page[:16] +
                f"{stable[0].device}:{stable[0].inode}".encode(),
            ).hexdigest()
            if self.generation and generation != self.generation:
                raise SnapshotError("Source generation changed; restart with reviewed state")
            self.generation = generation
            tick = time.perf_counter()
            plaintext = decrypt_database(encrypted, self.key, encrypted.with_name(encrypted.name+"-wal"))
            decrypt_ms = (time.perf_counter()-tick)*1000
            output = self.run_dir / (self.path.name + ".sqlite")
            temporary = output.with_suffix(".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(plaintext)
            os.replace(temporary, output)
        tick = time.perf_counter()
        connection = sqlite3.connect(f"file:{output}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise SnapshotError("Decrypted snapshot failed quick_check")
        finally:
            connection.close()
        cost = RefreshCost(self.name, reason, copy_ms, decrypt_ms,
                           (time.perf_counter()-tick)*1000, (time.process_time()-cpu)*1000,
                           (time.perf_counter()-started)*1000, copied, len(plaintext), attempt)
        return output, cost, stable



class SnapshotRepository:
    def __init__(self, keys: DatabaseKeys, cache: Path) -> None:
        self.keys = keys
        self.cache = private_dir(cache)
        self._databases: dict[str, SnapshotDatabase] = {}
        self.lock = threading.RLock()

    def source(self, relative: str) -> SnapshotDatabase:
        with self.lock:
            if relative not in self._databases:
                path = self.keys.path(relative)
                self._databases[relative] = SnapshotDatabase(
                    relative, path, self.keys.key(relative),
                    private_dir(self.cache / relative.replace('/', '_')),
                )
            return self._databases[relative]

    def refresh(self, relative: str, *, force: bool = False) -> Path:
        with self.lock:
            database = self.source(relative)
            output = database.run_dir / (database.path.name + '.sqlite')
            if not force and database.last_signature == signature(database.path) and output.exists():
                return output
            output, _cost, current = database.refresh('query')
            database.last_signature = current
            return output
