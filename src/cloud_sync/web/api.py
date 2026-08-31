"""FastAPI dashboard API. Read-only: it reports on runs the CLI already did.

No file transfer happens here — this app (and its Vercel deployment) only
ever reads the manifest.db / logs written locally by `cloud-sync run`. See
README "Dashboard data" for how the DB gets from your Mac to Vercel.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import cloud_sync_home, load_jobs
from ..engine.manifest import Manifest

app = FastAPI(title="cloud-sync dashboard")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"],
)


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
def get_jobs(config_path: str = "config/jobs.yaml"):
    try:
        jobs = load_jobs(config_path)
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
    manifest = _manifest()
    runs = manifest.recent_runs(job_name=job, limit=limit)
    manifest.close()
    return [RunOut(**r.__dict__) for r in runs]


@app.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int):
    manifest = _manifest()
    run = manifest.get_run(run_id)
    manifest.close()
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return RunOut(**run.__dict__)


@app.get("/api/runs/{run_id}/log")
def get_run_log(run_id: int, tail: int = 500):
    """Tail the JSON-lines log for the job this run belongs to."""
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


# Serve the single-page dashboard for local (non-Vercel) use:
# `uvicorn cloud_sync.web.api:app` then open http://localhost:8000/
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")
