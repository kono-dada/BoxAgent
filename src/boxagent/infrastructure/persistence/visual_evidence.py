"""Immutable local image sources, durable delivery state and deletion tombstones."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from boxagent.domain.memory.visual import VisualEvidence, VisualEpisode


class LocalVisualEvidenceStore:
    def __init__(self, root: Path, *, byte_budget: int = 128 * 1024 * 1024) -> None:
        self.root = root
        self.byte_budget = byte_budget
        self.lock = asyncio.Lock()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(self.root / "sources.sqlite")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        db.execute("""CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            memory_ids TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '',
            compiled TEXT NOT NULL DEFAULT '')""")
        columns = {row[1] for row in db.execute("PRAGMA table_info(evidence)")}
        if "compiled" not in columns:
            db.execute("ALTER TABLE evidence ADD COLUMN compiled TEXT NOT NULL DEFAULT ''")
        try:
            with db:
                yield db
        finally:
            db.close()

    async def put(self, evidence: VisualEvidence, image: bytes) -> VisualEvidence:
        async with self.lock:
            return await asyncio.to_thread(self._put, evidence, image)

    def _put(self, evidence: VisualEvidence, image: bytes) -> VisualEvidence:
        if not image or len(image) > 4 * 1024 * 1024 or hashlib.sha256(image).hexdigest() != evidence.sha256:
            raise ValueError("画面大小或校验无效")
        image_module = importlib.import_module("PIL.Image")
        with image_module.open(io.BytesIO(image)) as decoded:
            if decoded.format != "PNG" or decoded.width * decoded.height > 4_000_000:
                raise ValueError("画面必须是尺寸受限的 PNG")
            decoded.verify()
        with self._db() as db:
            existing = db.execute("SELECT status,payload FROM evidence WHERE id=?", (evidence.evidence_id,)).fetchone()
            if existing:
                if existing[0] in ("deleting", "deleted"):
                    raise ValueError("已删除的来源不能重新写入")
                return VisualEvidence.from_payload(json.loads(existing[1]))
            retained = {row[0] for row in db.execute("SELECT sha FROM evidence WHERE status <> 'deleted'")}
            for orphan in self.root.glob("*.png"):
                if orphan.stem not in retained:
                    orphan.unlink(missing_ok=True)
            destination = self.root / (evidence.sha256 + ".png")
            if not destination.exists():
                used = sum(path.stat().st_size for path in self.root.glob("*.png"))
                if used + len(image) > self.byte_budget:
                    raise ValueError("画面记忆空间已满，请删除不需要的经历")
                with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as staged:
                    staged.write(image)
                    staged.flush()
                    os.fsync(staged.fileno())
                    temporary = Path(staged.name)
                try:
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            db.execute("INSERT INTO evidence(id,payload,sha) VALUES(?,?,?)",
                       (evidence.evidence_id, json.dumps(evidence.payload(), ensure_ascii=False), evidence.sha256))
        return evidence

    async def pending(self) -> list[VisualEvidence]:
        async with self.lock:
            return await asyncio.to_thread(self._pending)

    def _pending(self) -> list[VisualEvidence]:
        with self._db() as db:
            rows = db.execute("SELECT payload FROM evidence WHERE status='pending' AND attempts<3 ORDER BY rowid").fetchall()
        return [VisualEvidence.from_payload(json.loads(row[0])) for row in rows]

    async def attempt(self, evidence_id: str) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._attempt, evidence_id)

    def _attempt(self, evidence_id: str) -> bool:
        with self._db() as db:
            cursor = db.execute("UPDATE evidence SET attempts=attempts+1 WHERE id=? AND status='pending' AND attempts<3",
                                (evidence_id,))
            return cursor.rowcount == 1

    async def bind(self, evidence_id: str, memory_ids: list[str]) -> None:
        async with self.lock:
            await asyncio.to_thread(self._update, evidence_id, "committed", memory_ids, "")

    async def failure(self, evidence_id: str, error: str) -> None:
        async with self.lock:
            await asyncio.to_thread(self._update, evidence_id, "pending", None, error[:200])

    async def compiled(self, evidence_id: str) -> VisualEpisode | None:
        async with self.lock:
            return await asyncio.to_thread(self._compiled, evidence_id)

    def _compiled(self, evidence_id: str) -> VisualEpisode | None:
        with self._db() as db:
            row = db.execute("SELECT compiled FROM evidence WHERE id=? AND status IN ('pending','committed')", (evidence_id,)).fetchone()
        return VisualEpisode.from_payload(json.loads(row[0])) if row and row[0] else None

    async def save_compiled(self, episode: VisualEpisode) -> None:
        async with self.lock:
            await asyncio.to_thread(self._save_compiled, episode)

    async def write_allowed(self, evidence_id: str) -> bool:
        async with self.lock:
            return await asyncio.to_thread(self._write_allowed, evidence_id)

    def _write_allowed(self, evidence_id: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT status FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        return bool(row and row[0] == "pending")

    def _save_compiled(self, episode: VisualEpisode) -> None:
        validated = VisualEpisode.from_payload(episode.payload())
        with self._db() as db:
            row = db.execute("SELECT payload FROM evidence WHERE id=? AND status='pending'", (episode.evidence.evidence_id,)).fetchone()
            if not row:
                return
            if VisualEvidence.from_payload(json.loads(row[0])) != episode.evidence:
                raise ValueError("经历不能替换来源证据")
            db.execute("UPDATE evidence SET compiled=? WHERE id=? AND status='pending' AND compiled=''",
                       (json.dumps(validated.payload(), ensure_ascii=False), episode.evidence.evidence_id))

    def _update(self, evidence_id: str, status: str, memory_ids: list[str] | None, error: str) -> None:
        with self._db() as db:
            db.execute("UPDATE evidence SET status=?, memory_ids=COALESCE(?,memory_ids), error=? WHERE id=? AND status='pending'",
                       (status, json.dumps(memory_ids) if memory_ids is not None else None, error, evidence_id))

    async def for_memory(self, memory_id: str) -> VisualEvidence | None:
        async with self.lock:
            return await asyncio.to_thread(self._for_memory, memory_id)

    def _for_memory(self, memory_id: str) -> VisualEvidence | None:
        with self._db() as db:
            rows = db.execute("SELECT payload,memory_ids FROM evidence WHERE status='committed'").fetchall()
        for payload, ids in rows:
            if memory_id in json.loads(ids):
                return VisualEvidence.from_payload(json.loads(payload))
        return None

    async def memory_ids(self, evidence_id: str) -> list[str]:
        async with self.lock:
            return await asyncio.to_thread(self._memory_ids, evidence_id)

    def _memory_ids(self, evidence_id: str) -> list[str]:
        with self._db() as db:
            row = db.execute("SELECT memory_ids FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        return json.loads(row[0]) if row else []

    async def read(self, evidence: VisualEvidence) -> bytes:
        async with self.lock:
            return await asyncio.to_thread(self._read, evidence)

    def _read(self, evidence: VisualEvidence) -> bytes:
        VisualEvidence.from_payload(evidence.payload())
        with self._db() as db:
            row = db.execute("SELECT status FROM evidence WHERE id=?", (evidence.evidence_id,)).fetchone()
        if not row or row[0] != "committed":
            raise ValueError("来源不存在或已删除")
        content = (self.root / (evidence.sha256 + ".png")).read_bytes()
        if hashlib.sha256(content).hexdigest() != evidence.sha256:
            raise ValueError("来源图片校验失败")
        return content

    async def tombstone(self, evidence_id: str) -> None:
        async with self.lock:
            await asyncio.to_thread(self._tombstone, evidence_id)

    def _tombstone(self, evidence_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE evidence SET status='deleting', payload='{}', compiled='', error='' WHERE id=?", (evidence_id,))

    async def deleting(self) -> list[tuple[str, list[str]]]:
        async with self.lock:
            return await asyncio.to_thread(self._deleting)

    def _deleting(self) -> list[tuple[str, list[str]]]:
        with self._db() as db:
            rows = db.execute("SELECT id,memory_ids FROM evidence WHERE status='deleting'").fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    async def finish_delete(self, evidence_id: str) -> None:
        async with self.lock:
            await asyncio.to_thread(self._finish_delete, evidence_id)

    def _finish_delete(self, evidence_id: str) -> None:
        with self._db() as db:
            row = db.execute("SELECT sha FROM evidence WHERE id=? AND status='deleting'", (evidence_id,)).fetchone()
            if not row:
                return
            other = db.execute("SELECT 1 FROM evidence WHERE sha=? AND id<>? AND status NOT IN ('deleting','deleted')",
                               (row[0], evidence_id)).fetchone()
            if not other:
                (self.root / (row[0] + ".png")).unlink(missing_ok=True)
            db.execute("UPDATE evidence SET status='deleted', memory_ids='[]', sha='' WHERE id=?", (evidence_id,))
