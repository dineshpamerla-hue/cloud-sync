"""Core sync loop: diff source vs manifest, upload what's missing or changed,
retry on transient failure, write structured JSON-lines logs the dashboard
can tail.

This is the one piece of the codebase that's allowed to take hours to run.
It is designed to be interrupted (Ctrl-C, laptop sleep, SSH drop) and resumed
by just calling `cloud-sync run --job <name>` again — the manifest is the
source of truth for what's already done.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..config import JobConfig, cloud_sync_home
from ..destinations import build_destination
from ..sources import build_source
from ..sources.local import LocalSource
from .manifest import Manifest, hash_file
from .organizer import build_key


@dataclass
class UploadStats:
    files_total: int = 0
    files_uploaded: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    bytes_uploaded: int = 0


class JsonLogger:
    """Structured JSON-lines logger, one file per run, so the dashboard can
    show a real progress log without parsing free-text output.
    """

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.log_path, "a", buffering=1)  # line-buffered

    def emit(self, event: str, **fields) -> None:
        record = {"ts": time.time(), "event": event, **fields}
        self._fh.write(json.dumps(record) + "\n")

    def close(self) -> None:
        self._fh.close()


MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


def run_job(
    job: JobConfig,
    manifest: Optional[Manifest] = None,
    max_retries: int = MAX_RETRIES,
    progress_cb: Optional[Callable[[UploadStats], None]] = None,
    dry_run: bool = False,
) -> UploadStats:
    """Run one job end-to-end: list source files, skip anything the manifest
    says is already uploaded and unchanged, upload the rest, record results.
    """
    home = cloud_sync_home()
    own_manifest = manifest is None
    manifest = manifest or Manifest(home / "manifest.db")
    logger = JsonLogger(home / "logs" / f"{job.name}.jsonl")

    source = build_source(job.source.type, {**job.source.options, "exclude": job.exclude}
                           if job.source.type == "local" else job.source.options)
    destination = build_destination(job.destination.type, job.destination.options)
    prefix_strategy = job.destination.options.get("prefix_strategy", "mirror")

    if hasattr(destination, "ensure_bucket") and not dry_run:
        destination.ensure_bucket()

    # Manifest-loss recovery: if this job has no recorded files (fresh machine,
    # a CI cache miss, a deleted DB), prime an in-memory set of keys already in
    # the destination so we record them as done instead of re-uploading
    # hundreds of GB. One rclone call; skipped when the manifest already has
    # state or the destination can't enumerate keys.
    existing_keys: set[str] = set()
    if not dry_run and not manifest.has_files(job.name) and hasattr(destination, "list_existing_keys"):
        try:
            existing_keys = destination.list_existing_keys()
            logger.emit("primed_existing_keys", job=job.name, count=len(existing_keys))
        except Exception as exc:
            # Non-fatal: worst case we re-upload. Log and continue.
            logger.emit("prime_existing_keys_failed", error=str(exc))

    run_id = manifest.start_run(job.name)
    stats = UploadStats()
    logger.emit("run_started", job=job.name, run_id=run_id, source=source.describe(),
                destination=destination.describe())

    run_status = "completed"
    try:
        for src_file in source.list_files():
            if job.exclude and job.source.type != "local":
                import fnmatch
                if any(fnmatch.fnmatch(src_file.relative_path, pat) for pat in job.exclude):
                    continue

            stats.files_total += 1
            dest_key = build_key(prefix_strategy, src_file.relative_path, src_file.mtime)

            if not manifest.needs_upload(job.name, src_file.relative_path, src_file.size):
                stats.files_skipped += 1
                logger.emit("skipped", path=src_file.relative_path, reason="already_uploaded")
                if progress_cb:
                    progress_cb(stats)
                continue

            # Recovered-from-destination shortcut: the manifest didn't know
            # about this file, but the key is already in the bucket. Record it
            # as uploaded (so future runs skip via the manifest) without
            # re-transferring the bytes.
            if dest_key in existing_keys:
                manifest.record_result(
                    job.name, src_file.relative_path, dest_key, "uploaded",
                    size_bytes=src_file.size,
                )
                stats.files_skipped += 1
                logger.emit("skipped", path=src_file.relative_path, reason="exists_in_destination")
                if progress_cb:
                    progress_cb(stats)
                continue

            if dry_run:
                logger.emit("would_upload", path=src_file.relative_path, dest_key=dest_key)
                stats.files_uploaded += 1
                if progress_cb:
                    progress_cb(stats)
                continue

            ok, content_hash, error = _upload_with_retry(
                source, destination, src_file, dest_key, max_retries, logger
            )
            if ok:
                manifest.record_result(
                    job.name, src_file.relative_path, dest_key, "uploaded",
                    content_hash=content_hash, size_bytes=src_file.size,
                )
                stats.files_uploaded += 1
                stats.bytes_uploaded += src_file.size or 0
            else:
                manifest.record_result(
                    job.name, src_file.relative_path, dest_key, "failed",
                    size_bytes=src_file.size, error=error,
                )
                stats.files_failed += 1

            manifest.update_run_counts(
                run_id, files_total=stats.files_total, files_uploaded=stats.files_uploaded,
                files_skipped=stats.files_skipped, files_failed=stats.files_failed,
                bytes_uploaded=stats.bytes_uploaded,
            )
            if progress_cb:
                progress_cb(stats)

        run_status = "completed_with_errors" if stats.files_failed else "completed"
    except Exception as exc:
        logger.emit("run_error", error=str(exc))
        run_status = "failed"
        raise
    finally:
        # Flush the final stats to the run record. update_run_counts is only
        # called after an actual upload/failure inside the loop, so a run that
        # only skips files (a resumed run where everything is already done)
        # would otherwise leave the run row at its 0 defaults.
        manifest.update_run_counts(
            run_id, files_total=stats.files_total, files_uploaded=stats.files_uploaded,
            files_skipped=stats.files_skipped, files_failed=stats.files_failed,
            bytes_uploaded=stats.bytes_uploaded,
        )
        manifest.finish_run(run_id, run_status)
        logger.emit("run_finished", status=run_status, **stats.__dict__)
        logger.close()
        if own_manifest:
            manifest.close()

    return stats


def _upload_with_retry(source, destination, src_file, dest_key, max_retries, logger):
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            if isinstance(source, LocalSource):
                local_path = source.abs_path(src_file)
                destination.upload_file(local_path, dest_key)
                with open(local_path, "rb") as f:
                    content_hash = hash_file(f)
            else:
                # Non-local sources (e.g. Google Drive) stream read() -> a temp
                # file -> upload_file(). Create the temp file first so a failure
                # while reading the stream can never leak it, and let source.read
                # raise if the underlying transfer (e.g. `rclone cat`) failed
                # rather than silently uploading a truncated file.
                import tempfile
                fd, tmp_name = tempfile.mkstemp()
                os.close(fd)
                hasher_path = Path(tmp_name)
                try:
                    with source.read(src_file) as stream, open(hasher_path, "wb") as tmp:
                        while chunk := stream.read(1024 * 1024):
                            tmp.write(chunk)
                    destination.upload_file(hasher_path, dest_key)
                    with open(hasher_path, "rb") as f:
                        content_hash = hash_file(f)
                finally:
                    hasher_path.unlink(missing_ok=True)

            logger.emit("uploaded", path=src_file.relative_path, dest_key=dest_key, attempt=attempt)
            return True, content_hash, None
        except Exception as exc:
            last_error = str(exc)
            logger.emit("upload_error", path=src_file.relative_path, attempt=attempt, error=last_error)
            if attempt < max_retries:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    return False, None, last_error


def verify_job(job: JobConfig, manifest: Optional[Manifest] = None, sample: Optional[int] = None):
    """Re-hash uploaded files and compare against the manifest's recorded hash.

    For local sources this re-reads the local file (cheap). For remote
    sources it re-downloads via source.read(), which is why `sample` exists —
    a full re-verify of hundreds of GB from Drive is expensive; pass a sample
    size to spot-check instead of re-checking everything.
    """
    home = cloud_sync_home()
    own_manifest = manifest is None
    manifest = manifest or Manifest(home / "manifest.db")
    logger = JsonLogger(home / "logs" / f"{job.name}.verify.jsonl")

    source = build_source(job.source.type, {**job.source.options, "exclude": job.exclude}
                           if job.source.type == "local" else job.source.options)

    records = list(manifest.files_for_job(job.name, status="uploaded"))
    if sample is not None:
        records = records[:sample]

    mismatches = []
    checked = 0
    try:
        for record in records:
            try:
                if isinstance(source, LocalSource):
                    with open(source.root / record.relative_path, "rb") as f:
                        actual_hash = hash_file(f)
                else:
                    class _Tmp:
                        relative_path = record.relative_path
                    with source.read(_Tmp()) as stream:
                        import hashlib
                        h = hashlib.sha256()
                        while chunk := stream.read(1024 * 1024):
                            h.update(chunk)
                        actual_hash = h.hexdigest()
            except FileNotFoundError:
                mismatches.append((record.relative_path, "source_file_missing"))
                logger.emit("verify_mismatch", path=record.relative_path, reason="source_file_missing")
                continue

            checked += 1
            if actual_hash != record.content_hash:
                mismatches.append((record.relative_path, "hash_mismatch"))
                logger.emit("verify_mismatch", path=record.relative_path, reason="hash_mismatch")
            else:
                manifest.record_result(
                    job.name, record.relative_path, record.dest_key, "verified",
                    content_hash=actual_hash, size_bytes=record.size_bytes,
                )
                logger.emit("verify_ok", path=record.relative_path)
    finally:
        logger.close()
        if own_manifest:
            manifest.close()

    return {"checked": checked, "mismatches": mismatches}
