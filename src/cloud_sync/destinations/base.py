"""Destination abstract base class."""
from __future__ import annotations

import abc
from pathlib import Path


class Destination(abc.ABC):
    """Anything cloud-sync can upload files to."""

    @abc.abstractmethod
    def exists(self, key: str) -> bool:
        """Whether `key` already exists at the destination."""
        raise NotImplementedError

    @abc.abstractmethod
    def upload_file(self, local_path: Path, key: str) -> None:
        """Upload the file at local_path to `key`. Implementations should be
        safe to retry (idempotent) since the engine will retry on failure.
        """
        raise NotImplementedError

    def describe(self) -> str:
        return self.__class__.__name__
