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
        proc = subprocess.Popen(
            ["rclone", "cat", f"{self.remote}{file.relative_path}"],
            stdout=subprocess.PIPE,
        )
        return proc.stdout  # type: ignore[return-value]
