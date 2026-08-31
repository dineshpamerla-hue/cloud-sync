"""Source abstract base class.

Every source adapter (local disk, Google Drive, and later Dropbox/S3/iCloud)
implements this same tiny interface. The engine only ever talks to a Source
through these three methods, so adding a new source never touches
engine/uploader.py.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Iterator, Optional


@dataclass
class SourceFile:
    """One file as seen by a source, before it's been hashed or uploaded."""

    # Path/identifier relative to the source root, using forward slashes.
    # This is what organizer.py and the manifest key off of.
    relative_path: str

    # Size in bytes, when known up front (helps progress reporting).
    size: Optional[int]

    # Best-known modified time, used for date-based organizing and as a
    # cheap "did this change" signal before a full hash is computed.
    mtime: Optional[datetime]

    # Opaque per-source identifier (e.g. Google Drive file ID) useful for
    # sources where relative_path alone isn't a stable key.
    source_id: Optional[str] = None


class Source(abc.ABC):
    """Anything cloud-sync can read files from."""

    @abc.abstractmethod
    def list_files(self) -> Iterator[SourceFile]:
        """Yield every file this source currently sees (after exclude filtering
        is applied by the caller — sources yield everything, the engine filters).
        """
        raise NotImplementedError

    @abc.abstractmethod
    def read(self, file: SourceFile) -> BinaryIO:
        """Return a binary file-like object positioned at the start of the file's
        content. Caller is responsible for closing it.
        """
        raise NotImplementedError

    def describe(self) -> str:
        """Human-readable description for logs/dashboard, e.g. 'local:~/Pictures'."""
        return self.__class__.__name__
