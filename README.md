# cloud-sync

A reusable CLI + web dashboard for copying files from any configured source
(local disk, Google Drive, and extensible to Dropbox/S3/iCloud/etc.) into
Backblaze B2 buckets — with resumable transfers, deduplication, a run
history, and a small dashboard to monitor jobs from anywhere.

## The one rule this whole design follows

**Large transfers run locally or on a machine you control — never inside a
Vercel serverless function.** Vercel hosts *only* the read-only dashboard
(job list, run history, log viewer). The actual byte-moving engine is the
`cloud-sync` CLI, which you run on your Mac (or later, a scheduled GitHub
Action or a small VM). The dashboard never triggers a transfer — it just
reports on ones the CLI already ran.

```
                          ┌─────────────────────┐
   your Mac (CLI)         │   Vercel (read-only) │
   ─────────────           ─────────────────────
   cloud-sync run    ──►   dashboard_data/manifest.db (pushed after each run)
   (rclone under the hood) │   FastAPI /api/* + static/index.html
   writes to               │   shows job list, run history, log tail
   ~/.cloud-sync/manifest.db
```

## Why rclone

Both the Google Drive source and the Backblaze B2 destination are
implemented as thin wrappers around `rclone` subprocess calls, rather than
`b2sdk` + `google-api-python-client`. You already have working, OAuth'd
`backblaze:` and `gdrive:` rclone remotes, so this means **zero new auth
flows** and one dependency to maintain instead of two SDKs with different
retry/pagination/quirks. Python still owns everything that makes this
"reusable" rather than a shell script: the manifest, the diff logic, retries,
structured logs, the job config format, and the pluggable adapter pattern.
If you'd rather remove the rclone dependency later, only
`sources/google_drive.py` and `destinations/backblaze_b2.py` need to change
— the engine, manifest, and CLI don't know or care how a Source/Destination
gets its bytes.

## Install

```bash
cd cloud-sync
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then fill in B2_KEY_ID / B2_APPLICATION_KEY
```

rclone must already be installed and have working `backblaze:` and
`gdrive:` remotes (you said you already have this). If you ever need to
redo it: `scripts/setup_rclone_remotes.sh`.

> **If you pasted a real Backblaze application key into this chat at any
> point, treat it as compromised.** Rotate it in the Backblaze console
> (App Keys → revoke, create a new one) and put only the new key in your
> local `.env`, which is gitignored and never leaves your machine.

## Define a job

Edit `config/jobs.yaml`. Two jobs are pre-defined for your setup:

- `photos-local` — `~/Pictures` → B2 bucket `dinu-photos`, organized as
  `YYYY/MM/filename` by file mtime.
- `gdrive-backup` — your `gdrive:` remote → B2 bucket `dinu-gdrive-backup`
  (already exists), mirroring Drive's folder structure, exporting native
  Docs/Sheets/Slides to docx/xlsx/pptx.

Adding a new source or destination later (Dropbox, S3, iCloud, ...) means
writing one small class implementing `Source` or `Destination`
(`src/cloud_sync/sources/base.py` / `destinations/base.py`) and registering
it in that package's `__init__.py` — no changes anywhere else.

## Run it

```bash
cloud-sync run --job photos-local          # first run: uploads everything
cloud-sync run --job photos-local          # re-run: skips unchanged files
cloud-sync run --job photos-local --dry-run   # see what would upload, no transfer

cloud-sync status                          # table of every job's last run

cloud-sync verify --job photos-local              # re-hash every uploaded file
cloud-sync verify --job photos-local --sample 200  # spot-check 200 instead
```

Interrupt any run with Ctrl-C at any point — it's safe. The SQLite manifest
at `~/.cloud-sync/manifest.db` tracks per-file status
(`pending`/`uploaded`/`failed`/`verified`), so re-running `cloud-sync run`
picks up exactly where it left off, and failed files get retried
automatically (up to 3 attempts with backoff) without re-uploading files
that already succeeded.

**Do not delete anything locally or in Drive until `cloud-sync verify`
comes back clean.**

For a full run of every configured job plus verification, use
`scripts/run_full_sync.sh` (also suitable for cron).

## Dashboard

**Locally:**
```bash
uvicorn cloud_sync.web.api:app --reload
# open http://localhost:8000
```

**On Vercel:**
```bash
npm i -g vercel   # if you don't have it
vercel deploy
```
`vercel.json` routes `/api/*` to `api/index.py` (the FastAPI app) and serves
`src/cloud_sync/web/static/` as the frontend. Connect the repo to GitHub in
the Vercel dashboard for auto-deploy on push.

### Dashboard data

Vercel's filesystem is read-only and ephemeral, so the deployed API can't
read `~/.cloud-sync/manifest.db` directly off your Mac. The simplest thing
that works for a single-user dashboard: `scripts/sync_dashboard_data.sh`
copies your local manifest into `dashboard_data/manifest.db` and commits +
pushes it, which Vercel then serves on its next auto-deploy.
`scripts/run_full_sync.sh` calls this automatically after every run. This
isn't real-time, but for "check my transfer status from my phone" it's
good enough with zero extra infra. If you outgrow it, swap in Vercel
Postgres or Blob storage — only `api/index.py`'s DB-location logic needs to
change, since all the actual query logic lives in `cloud_sync/web/api.py`.

## Repo hygiene

`.gitignore` excludes `.env`, `*.conf`, `rclone.conf`, `credentials.json`,
`*.db` (except the intentionally-committed `dashboard_data/manifest.db`
snapshot), and `logs/`. Nothing in this repo should ever contain a real key
— `.env.example` documents variable *names* only.

## Project layout

```
cloud-sync/
├── config/jobs.yaml            # declarative job definitions
├── src/cloud_sync/
│   ├── config.py                # loads jobs.yaml + .env
│   ├── sources/                 # Source ABC + local, google_drive adapters
│   ├── destinations/            # Destination ABC + backblaze_b2 adapter
│   ├── engine/
│   │   ├── manifest.py          # SQLite: per-file + per-run state
│   │   ├── organizer.py         # date/mirror key-building
│   │   └── uploader.py          # the sync loop: diff, upload, retry, log
│   ├── cli.py                   # run / status / verify
│   └── web/api.py               # FastAPI dashboard, served locally or on Vercel
├── api/index.py                 # Vercel entrypoint wrapping web/api.py
├── tests/                       # unit + end-to-end (fake destination, no network)
└── scripts/                     # rclone setup, full-sync cron wrapper, dashboard sync
```

## Tests

```bash
pytest
```
19 tests cover the manifest's resumability logic, the date/mirror key
builder, and an end-to-end run of the sync loop against a real local
filesystem with a fake in-memory destination (no network or rclone needed,
so this runs in CI).
