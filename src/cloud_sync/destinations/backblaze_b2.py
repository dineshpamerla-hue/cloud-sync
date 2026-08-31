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
            # rclone lsjson on a single file path errors out if it's missing —
            # treat any failure here as "doesn't exist" and let upload retry logic
            # surface a real error if it's something else.
            return False
        try:
            return bool(json.loads(result.stdout or "[]"))
        except json.JSONDecodeError:
            return False

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
