"""Local-disk source: walks a directory tree."""
from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from .base import Source, SourceFile


class LocalSource(Source):
    def __init__(self, path: str, exclude: list[str] | None = None):
        self.root = Path(path).expanduser().resolve()
        self.exclude = exclude or []
        if not self.root.exists():
            raise FileNotFoundError(f"local source path does not exist: {self.root}")

    def describe(self) -> str:
        return f"local:{self.root}"

    def _is_excluded(self, rel_path: str) -> bool:
        return any(fnmatch.fnmatch(rel_path, pattern) for pattern in self.exclude)

    def list_files(self) -> Iterator[SourceFile]:
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Skip common junk directories outright for speed.
            dirnames[:] = [d for d in dirnames if d not in (".Trashes", ".Spotlight-V100")]
            for filename in filenames:
                abs_path = Path(dirpath) / filename
                rel_path = abs_path.relative_to(self.root).as_posix()
                if self._is_excluded(rel_path):
                    continue
                try:
                    stat = abs_path.stat()
                except OSError:
                    # File vanished/permission denied mid-walk; skip it, don't crash the run.
                    continue
                yield SourceFile(
                    relative_path=rel_path,
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    source_id=None,
                )

    def read(self, file: SourceFile) -> BinaryIO:
        return open(self.root / file.relative_path, "rb")

    def abs_path(self, file: SourceFile) -> Path:
        """Used by the rclone-backed uploader, which needs a real filesystem path."""
        return self.root / file.relative_path
