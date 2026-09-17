"""FastAPI dashboard API.

Two responsibilities:

1. **Read-only reporting** on runs the CLI already did. Locally it reads the
   SQLite manifest written by `cloud-sync run`. On Vercel (read-only,
   ephemeral FS) it instead reads a committed JSON snapshot produced by
   `cloud-sync export-history` — set DASHBOARD_HISTORY_JSON to switch. No file
   transfer ever happens in this process.

2. **Triggering** a sync without keeping a laptop on: `POST /api/sync/start`
   dispatches a GitHub Actions workflow (which runs the CLI on a runner). This
   is gated by a shared secret and only ever talks to api.github.com. See
   docs/DEPLOY.md.
"""
from __future__ import annotations

import hmac
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import cloud_sync_home, load_jobs
from ..engine.manifest import Manifest

app = FastAPI(title="cloud-sync dashboard")

# CORS: the dashboard is same-origin with the API in every deployment we ship
# (Vercel serves both; locally uvicorn serves both). We therefore only need to
# allow the known dashboard origins, NOT "*", because a state-changing POST
# lives here now. Extra origins can be added via DASHBOARD_ALLOWED_ORIGINS
# (comma-separated).
_allowed_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
# Vercel injects these itself and rejects any custom name under the reserved
# VERCEL_ prefix, so the deployment origin has to be read, never configured.
for _host_var in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
    _host = os.environ.get(_host_var)
    if _host:
        _allowed_origins.append(f"https://{_host.rstrip('/')}")
_extra = os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "")
_allowed_origins.extend(o.strip().rstrip("/") for o in _extra.split(",") if o.strip())
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Fixed, code-controlled config location. NOT taken from the request: an
# attacker must never be able to point the API at an arbitrary path on disk.
JOBS_CONFIG_PATH = "config/jobs.yaml"

GITHUB_API = "https://api.github.com"
SYNC_WORKFLOW_FILE = "sync.yml"
# Jobs the web trigger is allowed to start. Local-only jobs (photos-local) are
# deliberately excluded — there is no ~/Pictures on a runner.
ALLOWED_TRIGGER_JOBS = {"gdrive-backup"}


# ---------------------------------------------------------------------------
# Data source: committed JSON snapshot (Vercel) vs live SQLite (local)
# ---------------------------------------------------------------------------

def _history_path() -> Optional[Path]:
    raw = os.environ.get("DASHBOARD_HISTORY_JSON")
    if not raw:
        return None
    path = Path(raw)
    return path if path.exists() else None


def _load_history() -> dict:
    path = _history_path()
    if path is None:
        return {"jobs": [], "runs": []}
    try:
        return json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return {"jobs": [], "runs": []}


def _manifest() -> Manifest:
    return Manifest(cloud_sync_home() / "manifest.db")


class JobOut(BaseModel):
    name: str
    source_type: str
    destination_type: str
    bucket: Optional[str] = None
    last_run: Optional[dict] = None


class RunOut(BaseModel):
    id: int
    job_name: str
    started_at: str
    finished_at: Optional[str]
    files_total: int
    files_uploaded: int
    files_skipped: int
    files_failed: int
    bytes_uploaded: int
    status: str


@app.get("/api/jobs", response_model=list[JobOut])
def get_jobs():
    if _history_path() is not None:
        return [JobOut(**j) for j in _load_history().get("jobs", [])]

    try:
        jobs = load_jobs(JOBS_CONFIG_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    manifest = _manifest()
    out = []
    for job in jobs:
        runs = manifest.recent_runs(job.name, limit=1)
        last = runs[0].__dict__ if runs else None
        out.append(JobOut(
            name=job.name,
            source_type=job.source.type,
            destination_type=job.destination.type,
            bucket=job.destination.options.get("bucket"),
            last_run=last,
        ))
    manifest.close()
    return out


@app.get("/api/runs", response_model=list[RunOut])
def get_runs(job: Optional[str] = None, limit: int = 20):
    if _history_path() is not None:
        runs = _load_history().get("runs", [])
        if job:
            runs = [r for r in runs if r.get("job_name") == job]
        return [RunOut(**r) for r in runs[:limit]]

    manifest = _manifest()
    runs = manifest.recent_runs(job_name=job, limit=limit)
    manifest.close()
    return [RunOut(**r.__dict__) for r in runs]


@app.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int):
    if _history_path() is not None:
        for r in _load_history().get("runs", []):
            if r.get("id") == run_id:
                return RunOut(**r)
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    manifest = _manifest()
    run = manifest.get_run(run_id)
    manifest.close()
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return RunOut(**run.__dict__)


@app.get("/api/runs/{run_id}/log")
def get_run_log(run_id: int, tail: int = 500):
    """Tail the JSON-lines log for the job this run belongs to.

    In JSON (deployed) mode there are no local logs — the real log lives in the
    GitHub Actions run — so we return an empty list. Locally we tail the file.
    """
    if _history_path() is not None:
        return {"lines": [], "note": "Logs live in the GitHub Actions run for deployed syncs."}

    manifest = _manifest()
    run = manifest.get_run(run_id)
    manifest.close()
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    log_path = cloud_sync_home() / "logs" / f"{run.job_name}.jsonl"
    if not log_path.exists():
        return {"lines": []}

    lines = log_path.read_text().splitlines()[-tail:]
    parsed = []
    for line in lines:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"lines": parsed}


# ---------------------------------------------------------------------------
# Sync trigger: dispatch a GitHub Actions run (the actual worker)
# ---------------------------------------------------------------------------

class SyncStartRequest(BaseModel):
    job: str = "gdrive-backup"
    skip_verify: bool = False


def _require_token(x_sync_token: Optional[str]) -> None:
    """Fail closed: if no secret is configured, nobody can trigger. Compare in
    constant time and never echo the token.
    """
    expected = os.environ.get("SYNC_TRIGGER_SECRET")
    if not expected:
        raise HTTPException(status_code=503, detail="sync trigger not configured")
    if not x_sync_token or not hmac.compare_digest(x_sync_token, expected):
        raise HTTPException(status_code=403, detail="invalid or missing sync token")


def _github_request(method: str, path: str, body: Optional[dict] = None) -> tuple[int, bytes]:
    """Talk to api.github.com only. `path` is a fixed, code-built path — never
    derived from a request in a way that could change the host.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    if not token or not repo:
        raise HTTPException(status_code=503, detail="GitHub trigger not configured")

    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "cloud-sync-dashboard")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=502, detail=f"GitHub unreachable: {exc.reason}")


@app.post("/api/sync/start", status_code=202)
def sync_start(req: SyncStartRequest, x_sync_token: Optional[str] = Header(default=None)):
    _require_token(x_sync_token)
    if req.job not in ALLOWED_TRIGGER_JOBS:
        raise HTTPException(status_code=400, detail=f"job '{req.job}' is not web-triggerable")

    repo = os.environ.get("GITHUB_REPO", "")
    status, raw = _github_request(
        "POST",
        f"/repos/{repo}/actions/workflows/{SYNC_WORKFLOW_FILE}/dispatches",
        {"ref": "main", "inputs": {"job": req.job, "skip_verify": str(req.skip_verify).lower()}},
    )
    if status != 204:
        raise HTTPException(status_code=502, detail=f"dispatch failed ({status})")
    return {"status": "dispatched", "job": req.job}


@app.get("/api/sync/status")
def sync_status(x_sync_token: Optional[str] = Header(default=None)):
    _require_token(x_sync_token)
    repo = os.environ.get("GITHUB_REPO", "")
    status, raw = _github_request(
        "GET", f"/repos/{repo}/actions/workflows/{SYNC_WORKFLOW_FILE}/runs?per_page=3"
    )
    if status != 200:
        raise HTTPException(status_code=502, detail=f"could not read runs ({status})")
    try:
        runs = json.loads(raw).get("workflow_runs", [])
    except json.JSONDecodeError:
        runs = []
    return {
        "runs": [
            {
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "html_url": r.get("html_url"),
                "created_at": r.get("created_at"),
            }
            for r in runs
        ]
    }


# Serve the single-page dashboard for local (non-Vercel) use:
# `uvicorn cloud_sync.web.api:app` then open http://localhost:8000/
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
