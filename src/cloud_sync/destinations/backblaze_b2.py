"""Backblaze B2 destination.

Implemented on top of rclone's B2 backend (an rclone remote named `backblaze:`
is assumed to already exist and be configured with your B2 keyID/appKey —
see scripts/setup_rclone_remotes.sh). This mirrors the Google Drive source's
approach and means the whole engine has exactly one external dependency
(rclone) instead of juggling b2sdk + google-api-python-client separately.
That tradeoff is spelled out in the README.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class BackblazeB2Destination:
    def __init__(self, bucket: str, prefix_strategy: str = "mirror", remote: str = "backblaze:"):
        if not remote.endswith(":"):
            remote = remote + ":"
        self.remote = remote
        self.bucket = bucket
        self.prefix_strategy = prefix_strategy  # consumed by engine/organizer.py, kept here for describe()
        if shutil.which("rclone") is None:
            raise RuntimeError(
                "rclone not found on PATH. Install it (`brew install rclone`) and run "
                "scripts/setup_rclone_remotes.sh first."
            )

    def describe(self) -> str:
        return f"backblaze_b2:{self.bucket} ({self.prefix_strategy})"

    def _remote_path(self, key: str) -> str:
        return f"{self.remote}{self.bucket}/{key}"

    def exists(self, key: str) -> bool:
        result = subprocess.run(
            ["rclone", "lsjson", self._remote_path(key)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            # rclone reports a genuinely-missing path as "directory not found" /
            # "object not found" — those legitimately mean "doesn't exist".
            # Anything else (auth failure, network error, bad bucket) is a real
            # error we must not silently treat as "missing", or the engine would
            # happily re-upload or skip based on a lie.
            stderr = (result.stderr or "").lower()
            if "not found" in stderr or "directory not found" in stderr:
                return False
            raise RuntimeError(
                f"rclone lsjson failed for {key}: {result.stderr.strip()}"
            )
        try:
            return bool(json.loads(result.stdout or "[]"))
        except json.JSONDecodeError:
            return False

    def list_existing_keys(self, prefix: str = "") -> set[str]:
        """Return the set of keys already present under this bucket/prefix.

        One `rclone lsjson --recursive` call. Used by the engine to recover
        when the manifest is empty (e.g. a fresh CI runner whose cache missed)
        so a lost manifest doesn't cause a full re-upload — keys already in B2
        are recorded as done instead of re-transferred.
        """
        target = f"{self.remote}{self.bucket}"
        if prefix:
            target = f"{target}/{prefix}"
        result = subprocess.run(
            ["rclone", "lsjson", "--recursive", "--files-only", target],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").lower()
            if "not found" in stderr:
                return set()
            raise RuntimeError(
                f"rclone lsjson --recursive failed for {target}: {result.stderr.strip()}"
            )
        try:
            entries = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return set()
        keys = set()
        for entry in entries:
            path = entry.get("Path")
            if path:
                keys.add(f"{prefix}/{path}" if prefix else path)
        return keys

    def upload_file(self, local_path: Path, key: str) -> None:
        result = subprocess.run(
            ["rclone", "copyto", str(local_path), self._remote_path(key)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"rclone upload failed for {key}: {result.stderr.strip()}")

    def ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist yet. Safe to call every run."""
        result = subprocess.run(
            ["rclone", "lsjson", self.remote],
            capture_output=True, text=True,
        )
        existing = set()
        if result.returncode == 0:
            try:
                existing = {entry["Path"] for entry in json.loads(result.stdout or "[]")}
            except json.JSONDecodeError:
                pass
        if self.bucket in existing:
            return
        result = subprocess.run(
            ["rclone", "mkdir", f"{self.remote}{self.bucket}"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"failed to create bucket {self.bucket}: {result.stderr.strip()}")
