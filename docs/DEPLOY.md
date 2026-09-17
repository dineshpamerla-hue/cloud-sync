# Deploying cloud-sync (dashboard + web-triggered sync)

This sets up the architecture where **you press a button in the web dashboard
and a Google Drive → Backblaze B2 sync runs on a GitHub Actions runner** — your
Mac doesn't need to stay on. Vercel hosts the dashboard and a thin trigger
endpoint; GitHub Actions is the worker that actually moves bytes.

```
  browser ──"Start sync"──► Vercel API ──GitHub API──► GitHub Actions runner
  (dashboard)                (trigger,               (cloud-sync run --job
                              secret-gated)            gdrive-backup, rclone)
                                                             │
        dashboard ◄──reads──── dashboard_data/history.json ◄─┘ (committed by CI)
```

Nothing secret is committed. Credentials live only in Vercel env vars and
GitHub Actions secrets.

---

## 1. Push the repo to GitHub

Done — the repo lives at `dineshpamerla-hue/cloud-sync` and `main` tracks it.
Just make sure your latest commits are pushed before deploying:

```bash
git push origin main
```

## 2. GitHub Actions secrets (the worker's credentials)

Repo → **Settings → Secrets and variables → Actions → New repository secret**.
Add three:

| Secret | How to get its value |
|---|---|
| `RCLONE_CONF_BASE64` | `base64 -i ~/.config/rclone/rclone.conf \| pbcopy` — paste. This is your existing `backblaze:` + `gdrive:` remotes. |
| `B2_KEY_ID` | Your Backblaze keyID (same as local `.env`). |
| `B2_APPLICATION_KEY` | Your Backblaze applicationKey. |

> Your `rclone.conf` holds the Google Drive OAuth token. Keeping it in GitHub
> Actions secrets (encrypted, not printed in logs) is the tradeoff for
> laptop-free syncs. If that's not acceptable, keep syncs local instead.

## 3. Create a fine-grained GitHub token (for the dashboard to dispatch runs)

github.com → **Settings → Developer settings → Fine-grained tokens → Generate**:

- **Resource owner:** you. **Repository access:** *Only select repositories* → `cloud-sync`.
- **Permissions → Actions:** *Read and write*. (Nothing else.)
- Copy the token — you'll paste it into Vercel next.

## 4. Deploy to Vercel + set env vars

1. vercel.com → **Add New → Project → Import** the `cloud-sync` repo. It reads `vercel.json`; no build command needed.
2. Project → **Settings → Environment Variables**, add:

| Name | Value |
|---|---|
| `GITHUB_TOKEN` | the fine-grained PAT from step 3 |
| `GITHUB_REPO` | `dineshpamerla-hue/cloud-sync` |
| `SYNC_TRIGGER_SECRET` | a long random string you invent (e.g. `openssl rand -hex 24`) — you'll type it into the dashboard once |

Those three are all you add. `CLOUD_SYNC_READONLY` and `DASHBOARD_HISTORY_JSON`
are set by `api/index.py`; the deployment's own origin is read from Vercel's
`VERCEL_URL` / `VERCEL_PROJECT_PRODUCTION_URL`, which Vercel injects for you.
(Don't try to create a `VERCEL_*` variable yourself — the platform reserves that
prefix and rejects it.) To allow an extra origin, set `DASHBOARD_ALLOWED_ORIGINS`
to a comma-separated list.

3. **Redeploy** so the env vars take effect.

## 5. Use it

- Open your Vercel URL. The dashboard loads from `dashboard_data/history.json`
  (empty until the first run).
- Click **Start Google Drive → B2 sync**. It prompts once for the
  `SYNC_TRIGGER_SECRET` (stored in your browser's localStorage), then dispatches
  the workflow and shows a link to the live Actions run.
- When the run finishes it commits an updated `history.json`; Vercel
  auto-redeploys and the dashboard shows the run.

## Limits & notes

- GitHub-hosted job cap is **6 hours**; the workflow sets `timeout-minutes: 350`.
  A first full sync of hundreds of GB may need more than one dispatch — the
  manifest cache makes re-runs resume where they left off.
- Google Drive enforces ~**750 GB/day** of download and per-file quotas.
- Public-repo Actions minutes are free; be mindful of GitHub's fair-use for
  very large/frequent transfers.
- `photos-local` is **not** web-triggerable (no `~/Pictures` on a runner) —
  run that one locally with `cloud-sync run --job photos-local`.
