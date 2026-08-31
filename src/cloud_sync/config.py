"""Loads config/jobs.yaml plus environment variables (.env) into typed objects.

Nothing in this module talks to the network. It is pure parsing/validation so
it stays trivially testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def cloud_sync_home() -> Path:
    """Root directory for the manifest DB and logs.

    Defaults to ~/.cloud-sync, overridable via CLOUD_SYNC_HOME so tests and
    CI don't touch a real home directory.

    On a read-only/ephemeral host (Vercel's serverless FS), set
    CLOUD_SYNC_READONLY=1 so we don't try to create the directory or its
    `logs/` subdir — the dashboard there reads run history from a committed
    JSON snapshot (DASHBOARD_HISTORY_JSON), never from this path.
    """
    load_dotenv(override=False)
    home = os.environ.get("CLOUD_SYNC_HOME")
    path = Path(home).expanduser() if home else Path.home() / ".cloud-sync"
    if os.environ.get("CLOUD_SYNC_READONLY"):
        return path
    path.mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class SourceConfig:
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class DestinationConfig:
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobConfig:
    name: str
    source: SourceConfig
    destination: DestinationConfig
    exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "JobConfig":
        if "name" not in raw:
            raise ValueError("job is missing required 'name' field")
        for key in ("source", "destination"):
            if key not in raw:
                raise ValueError(f"job '{raw['name']}' is missing required '{key}' field")

        src_raw = dict(raw["source"])
        src_type = src_raw.pop("type", None)
        if not src_type:
            raise ValueError(f"job '{raw['name']}' source is missing 'type'")

        dst_raw = dict(raw["destination"])
        dst_type = dst_raw.pop("type", None)
        if not dst_type:
            raise ValueError(f"job '{raw['name']}' destination is missing 'type'")

        return cls(
            name=raw["name"],
            source=SourceConfig(type=src_type, options=src_raw),
            destination=DestinationConfig(type=dst_type, options=dst_raw),
            exclude=list(raw.get("exclude", [])),
        )


def load_jobs(path: str | Path = "config/jobs.yaml") -> list[JobConfig]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"job config not found at {path}. Copy config/jobs.yaml or pass --config."
        )
    with path.open() as f:
        raw = yaml.safe_load(f) or {}

    jobs_raw = raw.get("jobs", [])
    if not jobs_raw:
        raise ValueError(f"{path} defines no jobs")

    jobs = [JobConfig.from_dict(j) for j in jobs_raw]

    names = [j.name for j in jobs]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate job names in {path}: {sorted(dupes)}")

    return jobs


def get_job(name: str, path: str | Path = "config/jobs.yaml") -> JobConfig:
    jobs = load_jobs(path)
    for job in jobs:
        if job.name == name:
            return job
    available = ", ".join(j.name for j in jobs)
    raise KeyError(f"no job named '{name}' in {path}. Available: {available}")


@dataclass
class B2Credentials:
    key_id: str
    application_key: str

    @classmethod
    def from_env(cls) -> "B2Credentials":
        load_dotenv(override=False)
        key_id = os.environ.get("B2_KEY_ID")
        app_key = os.environ.get("B2_APPLICATION_KEY")
        if not key_id or not app_key:
            raise RuntimeError(
                "B2_KEY_ID / B2_APPLICATION_KEY not set. Copy .env.example to .env "
                "and fill them in (get them from Backblaze -> App Keys)."
            )
        return cls(key_id=key_id, application_key=app_key)
