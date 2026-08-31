"""Vercel Python serverless entrypoint.

Wraps the same FastAPI app used locally. Vercel's filesystem is read-only and
ephemeral per-invocation, so:

  * CLOUD_SYNC_READONLY=1 tells config.cloud_sync_home() not to try to create
    ~/.cloud-sync or its logs/ subdir (that would 500 on Vercel).
  * DASHBOARD_HISTORY_JSON points the API at the committed run-history snapshot
    (written by `cloud-sync export-history`, pushed by the GitHub Actions
    worker) instead of a live SQLite manifest.

All query/trigger logic lives in cloud_sync.web.api — this file only adapts the
environment, so there's exactly one implementation to maintain.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Vercel's Python runtime needs `src/` on the path since this file lives
# outside the package.
_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root / "src"))

os.environ.setdefault("CLOUD_SYNC_READONLY", "1")
os.environ.setdefault(
    "DASHBOARD_HISTORY_JSON", str(_root / "dashboard_data" / "history.json")
)

from cloud_sync.web.api import app  # noqa: E402

# Vercel's Python builder looks for a top-level ASGI/WSGI `app` object.
__all__ = ["app"]
