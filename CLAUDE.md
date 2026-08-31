# CLAUDE.md — cloud-sync

Guidance for Claude Code (and humans) working in this repo. Keep it current;
the session-size hook will remind you to update it when a session grows large.

## What this is

A reusable CLI + read-only web dashboard that copies files from a configured
**source** (local disk, Google Drive) into **Backblaze B2**, with a resumable
SQLite manifest, dedup, retries, and run history. `rclone` does the actual
byte-moving (existing `backblaze:` and `gdrive:` remotes); Python owns the
manifest, diff logic, job config, and adapters.

## The one rule

**Large transfers never run inside a Vercel function** (serverless timeout, no
`rclone`, read-only FS). Two places run a sync:
- **Locally:** `cloud-sync run --job <name>` on your Mac.
- **On GitHub Actions:** the dashboard's "Start sync" button → a Vercel trigger
  endpoint → `workflow_dispatch` of `.github/workflows/sync.yml`, which runs the
  CLI on a runner (so your laptop can be off). Only `gdrive-backup` (cloud→cloud)
  is web-triggerable; `photos-local` is local-only.

Vercel hosts only the dashboard + the thin, secret-gated trigger proxy. It reads
run history from the committed `dashboard_data/history.json` snapshot (produced
by `cloud-sync export-history`), never a live DB.

## Architecture

```
src/cloud_sync/
  config.py            loads jobs.yaml + .env; cloud_sync_home() (CLOUD_SYNC_READONLY skips mkdir on Vercel)
  sources/             Source ABC + local, google_drive (rclone) adapters
  destinations/        Destination ABC + backblaze_b2 (rclone) adapter; list_existing_keys() for manifest recovery
  engine/
    manifest.py        SQLite per-file + per-run state; has_files() gates the recovery path
    organizer.py       date/mirror dest-key building (pure)
    uploader.py        the sync loop: diff, upload, retry, log; run counts flushed in finally
  cli.py               run / status / verify / export-history
  web/api.py           FastAPI dashboard + sync trigger (JSON snapshot on Vercel, SQLite locally)
api/index.py           Vercel entrypoint (sets CLOUD_SYNC_READONLY + DASHBOARD_HISTORY_JSON)
.github/workflows/sync.yml   the Actions worker
docs/DEPLOY.md         full Vercel + Actions + secrets setup
```

Adding a source/destination = one adapter class + one REGISTRY line; nothing
else changes.

## Running it

```bash
pip install -e ".[dev]"          # in a venv
cloud-sync run --job photos-local
cloud-sync run --job gdrive-backup --dry-run
cloud-sync verify --job photos-local --sample 200
cloud-sync status
cloud-sync export-history --out dashboard_data/history.json
uvicorn cloud_sync.web.api:app --reload    # dashboard at http://localhost:8000
```

## Tests

```bash
.venv/bin/pytest -q
```
No network/rclone needed — sources/destinations are faked. Add a test when you
change the manifest, the upload loop, key building, or a CLI command.

## Secrets / env

Nothing secret is committed. `.env` (gitignored) holds `B2_KEY_ID` /
`B2_APPLICATION_KEY` for local runs. Deployment secrets live in Vercel env vars
and GitHub Actions secrets — see `.env.example` and `docs/DEPLOY.md`. The full
manifest (`dashboard_data/manifest.db`, which contains Drive file paths) is
gitignored; only the runs-only `history.json` is committed.

## Review tooling

- `.claude/hooks/size-reminder.py` (Stop hook) nudges to update this file + `/compact`.
- `.claude/agents/security-auditor.md` and `code-issue-finder.md` — project
  subagents that audit and auto-fix; run before a push. Built-in `/security-review`
  and `/code-review` also apply.

## Known follow-ups (rclone-dependent, need a live remote to resolve)

- `google_drive.py read()` doesn't pass `--drive-export-formats`, so verifying a
  native Google Doc via `rclone cat` on its exported path may fail.
- `uploader.verify_job` treats only `FileNotFoundError` as "missing"; a remote
  file that's gone now raises `RuntimeError` and aborts verify instead of being
  recorded as `source_file_missing`.
