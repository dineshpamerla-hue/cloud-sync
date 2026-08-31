"""Google Drive source, implemented on top of an already-authorized rclone
remote (rclone handles the OAuth dance once, via `rclone config`; we just
shell out to it). This matches the "reuse what I already have" requirement —
it assumes a working `gdrive:` remote already exists in ~/.config/rclone/rclone.conf.

Native Google Docs/Sheets/Slides are exported by rclone to the formats given
in jobs.yaml's `export_formats` (default: docx/xlsx/pptx) since B2 can't
store a native Google Doc.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from typing import BinaryIO, Iterator

from .base import Source, SourceFile

DEFAULT_EXPORT_FORMATS = {
    "document": "docx",
    "spreadsheet": "xlsx",
    "presentation": "pptx",
}


class GoogleDriveSource(Source):
    def __init__(self, remote: str, export_formats: dict[str, str] | None = None):
        if not remote.endswith(":"):
            remote = remote + ":"
        self.remote = remote
        self.export_formats = export_formats or DEFAULT_EXPORT_FORMATS
        if shutil.which("rclone") is None:
            raise RuntimeError(
                "rclone not found on PATH. Install it (`brew install rclone`) and run "
                "scripts/setup_rclone_remotes.sh first."
            )

    def describe(self) -> str:
        return f"google_drive:{self.remote}"

    def _rclone_export_args(self) -> list[str]:
        """Build --drive-export-formats so native Docs/Sheets/Slides come through
        as real files instead of being skipped by rclone.
        """
        formats = ",".join(sorted(set(self.export_formats.values())))
        return ["--drive-export-formats", formats] if formats else []

    def list_files(self) -> Iterator[SourceFile]:
        cmd = [
            "rclone", "lsjson", "--recursive", "--files-only",
            *self._rclone_export_args(),
            self.remote,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"rclone lsjson failed for {self.remote}: {result.stderr.strip()}")

        for entry in json.loads(result.stdout or "[]"):
            mtime = None
            if entry.get("ModTime"):
                try:
                    mtime = datetime.fromisoformat(entry["ModTime"].replace("Z", "+00:00"))
                except ValueError:
                    mtime = None
            yield SourceFile(
                relative_path=entry["Path"],
                size=entry.get("Size"),
                mtime=mtime or datetime.now(timezone.utc),
                source_id=entry.get("ID"),
            )

    def read(self, file: SourceFile) -> BinaryIO:
        # Stream the file's bytes via `rclone cat` rather than downloading to a
        # temp file first. Fine for the manifest's verify/hash sampling use case;
        # the bulk transfer itself goes through `rclone copy` directly (see
        # engine/uploader.py) which is far more efficient for hundreds of GB.
        #
        # Wrapped so that closing the stream reaps the subprocess and raises if
        # `rclone cat` exited non-zero — otherwise a failed/partial transfer
        # would look like a short-but-successful read and we'd hash/upload a
        # truncated file.
        proc = subprocess.Popen(
            ["rclone", "cat", f"{self.remote}{file.relative_path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return _RcloneCatStream(proc)  # type: ignore[return-value]


class _RcloneCatStream:
    """Read-only wrapper over `rclone cat`'s stdout that verifies the process
    exit code when the stream is closed. Supports the context-manager and
    read() interface the engine and verify path use.
    """

    def __init__(self, proc: "subprocess.Popen[bytes]"):
        self._proc = proc
        self._stdout = proc.stdout

    def read(self, size: int = -1) -> bytes:
        assert self._stdout is not None
        return self._stdout.read(size)

    def close(self) -> None:
        if self._stdout is not None:
            self._stdout.close()
        stderr = b""
        if self._proc.stderr is not None:
            stderr = self._proc.stderr.read()
            self._proc.stderr.close()
        returncode = self._proc.wait()
        if returncode != 0:
            raise RuntimeError(
                f"rclone cat failed (exit {returncode}): {stderr.decode(errors='replace').strip()}"
            )

    def __enter__(self) -> "_RcloneCatStream":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # If the body already failed, don't mask that exception with a close()
        # error — best-effort reap and let the original propagate.
        if exc_type is not None:
            try:
                if self._stdout is not None:
                    self._stdout.close()
                if self._proc.stderr is not None:
                    self._proc.stderr.close()
                self._proc.wait()
            except Exception:
                pass
            return
        self.close()
