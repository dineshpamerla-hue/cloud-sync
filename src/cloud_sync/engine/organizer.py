"""Builds the destination key (path within the bucket) for a source file,
based on the job's prefix_strategy.

Kept as pure functions with no I/O so it's trivial to unit test.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Optional

# Extensions treated as "photo/video with reliable EXIF/creation metadata"
# vs. everything else, which just falls back to mtime. We don't read EXIF
# bytes ourselves (that's a job for a real EXIF library the CLI installs
# on demand) — callers pass in an already-resolved `taken_at` when they
# have it (e.g. from exifread), and we fall back to mtime otherwise.


def date_prefixed_key(relative_path: str, when: Optional[datetime]) -> str:
    """bucket/YYYY/MM/filename — used for prefix_strategy: date."""
    filename = PurePosixPath(relative_path).name
    if when is None:
        return f"unknown-date/{filename}"
    return f"{when:%Y}/{when:%m}/{filename}"


def mirror_key(relative_path: str) -> str:
    """Preserve the source's own folder structure — used for prefix_strategy: mirror."""
    return relative_path


def build_key(strategy: str, relative_path: str, when: Optional[datetime] = None) -> str:
    if strategy == "date":
        return date_prefixed_key(relative_path, when)
    if strategy == "mirror":
        return mirror_key(relative_path)
    raise ValueError(f"unknown prefix_strategy '{strategy}' (expected 'date' or 'mirror')")
