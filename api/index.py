"""Vercel Python serverless entrypoint.

Wraps the same FastAPI app used locally, but Vercel's filesystem is
read-only and ephemeral per-invocation, so this does NOT read the manifest
from ~/.cloud-sync like the local CLI does — it reads from wherever
DASHBOARD_DB_PATH points (a copy pushed up after each local run; see the
README's "Dashboard data" section for the simplest option: committing a
snapshot to the repo, or wiring up Vercel Postgres/Blob for something more
real-time). This file only adapts *how the DB is located*; all the actual
query logic lives in cloud_sync.web.api so there's exactly one
implementation to maintain.
"""
from __future__ import annotations

import os

# Vercel's Python runtime needs `src/` on the path since this file lives
# outside the package.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

os.environ.setdefault("CLOUD_SYNC_HOME", str(Path(__file__).parent.parent / "dashboard_data"))

from cloud_sync.web.api import app  # noqa: E402

# Vercel's Python builder looks for a top-level ASGI/WSGI `app` object.
__all__ = ["app"]
