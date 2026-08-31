"""SQLite manifest: tracks every file cloud-sync has ever seen, per job.

This is what makes runs resumable and safe to interrupt: re-running a job
skips any (job, relative_path) whose content hash and size match what's
already recorded as uploaded.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    job_name        TEXT NOT NULL,
    relative_path   TEXT NOT NULL,
    dest_key        TEXT NOT NULL,
    content_hash    TEXT,
    size_bytes      INTEGER,
    status          TEXT NOT NULL CHECK(status IN ('pending','uploaded','failed','verified')),
    error           TEXT,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (job_name, relative_path)
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    files_total     INTEGER NOT NULL DEFAULT 0,
    files_uploaded  INTEGER NOT NULL DEFAULT 0,
    files_skipped   INTEGER NOT NULL DEFAULT 0,
    files_failed    INTEGER NOT NULL DEFAULT 0,
    bytes_uploaded  INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','completed','completed_with_errors','failed'))
);
"""


def hash_file(fileobj: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    """SHA-256 of a file-like object's contents. Streams so large files never
    get fully loaded into memory.
    """
    h = hashlib.sha256()
    while chunk := fileobj.read(chunk_size):
        h.update(chunk)
    return h.hexdigest()


@dataclass
class FileRecord:
    job_name: str
    relative_path: str
    dest_key: str
    content_hash: Optional[str]
    size_bytes: Optional[int]
    status: str
    error: Optional[str]
    updated_at: str


@dataclass
class RunRecord:
    id: int
    job_name: str
    started_at: str
    finished_at: Optional[str]
    files_total: int
    files_uploaded: int
    files_skipped: int
    files_failed: int
    bytes_uploaded: int
    status: str


class Manifest:
    """Thin wrapper around a SQLite DB. One DB serves every job."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # -- file records ----------------------------------------------------

    def get_file(self, job_name: str, relative_path: str) -> Optional[FileRecord]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT * FROM files WHERE job_name=? AND relative_path=?",
                (job_name, relative_path),
            )
            row = cur.fetchone()
        return FileRecord(**dict(row)) if row else None

    def needs_upload(self, job_name: str, relative_path: str, size_bytes: Optional[int]) -> bool:
        """True if this file has never been uploaded, previously failed, or its
        size changed since the last recorded upload (a cheap, hash-free change
        signal used before doing the expensive full hash+upload).
        """
        record = self.get_file(job_name, relative_path)
        if record is None:
            return True
        if record.status not in ("uploaded", "verified"):
            return True
        if size_bytes is not None and record.size_bytes != size_bytes:
            return True
        return False

    def record_result(
        self,
        job_name: str,
        relative_path: str,
        dest_key: str,
        status: str,
        content_hash: Optional[str] = None,
        size_bytes: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._cursor() as cur:
            cur.execute(
                """
                INSERT INTO files (job_name, relative_path, dest_key, content_hash,
                                    size_bytes, status, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_name, relative_path) DO UPDATE SET
                    dest_key=excluded.dest_key,
                    content_hash=excluded.content_hash,
                    size_bytes=excluded.size_bytes,
                    status=excluded.status,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (job_name, relative_path, dest_key, content_hash, size_bytes, status, error, now),
            )

    def files_for_job(self, job_name: str, status: Optional[str] = None) -> Iterator[FileRecord]:
        with self._cursor() as cur:
            if status:
                cur.execute(
                    "SELECT * FROM files WHERE job_name=? AND status=? ORDER BY relative_path",
                    (job_name, status),
                )
            else:
                cur.execute(
                    "SELECT * FROM files WHERE job_name=? ORDER BY relative_path", (job_name,)
                )
            rows = cur.fetchall()
        for row in rows:
            yield FileRecord(**dict(row))

    # -- run records -------------------------------------------------------

    def start_run(self, job_name: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO runs (job_name, started_at, status) VALUES (?, ?, 'running')",
                (job_name, now),
            )
            return cur.lastrowid

    def update_run_counts(
        self, run_id: int, *, files_total=None, files_uploaded=None,
        files_skipped=None, files_failed=None, bytes_uploaded=None,
    ) -> None:
        fields, values = [], []
        for name, val in (
            ("files_total", files_total), ("files_uploaded", files_uploaded),
            ("files_skipped", files_skipped), ("files_failed", files_failed),
            ("bytes_uploaded", bytes_uploaded),
        ):
            if val is not None:
                fields.append(f"{name}=?")
                values.append(val)
        if not fields:
            return
        values.append(run_id)
        with self._cursor() as cur:
            cur.execute(f"UPDATE runs SET {', '.join(fields)} WHERE id=?", values)

    def finish_run(self, run_id: int, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE runs SET finished_at=?, status=? WHERE id=?", (now, status, run_id)
            )

    def recent_runs(self, job_name: Optional[str] = None, limit: int = 20) -> list[RunRecord]:
        with self._cursor() as cur:
            if job_name:
                cur.execute(
                    "SELECT * FROM runs WHERE job_name=? ORDER BY id DESC LIMIT ?",
                    (job_name, limit),
                )
            else:
                cur.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
            rows = cur.fetchall()
        return [RunRecord(**dict(row)) for row in rows]

    def get_run(self, run_id: int) -> Optional[RunRecord]:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE id=?", (run_id,))
            row = cur.fetchone()
        return RunRecord(**dict(row)) if row else None
