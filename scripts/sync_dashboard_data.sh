#!/usr/bin/env bash
# Simplest possible way to get run history onto the Vercel dashboard for a
# single-user setup: copy the local manifest.db into the repo's
# dashboard_data/ folder and push. Vercel redeploys automatically on push
# (repo connected to GitHub) and the API reads from that committed copy.
#
# This trades real-time-ness for zero infra — good enough for "check my
# transfer status from my phone" use. If you outgrow it, swap this script
# for a push to Vercel Postgres/Blob and leave api/index.py's DB-location
# logic as the only thing that needs to change.
set -euo pipefail
cd "$(dirname "$0")/.."

CLOUD_SYNC_HOME="${CLOUD_SYNC_HOME:-$HOME/.cloud-sync}"
mkdir -p dashboard_data
cp "$CLOUD_SYNC_HOME/manifest.db" dashboard_data/manifest.db

git add dashboard_data/manifest.db
if git diff --cached --quiet; then
  echo "No changes to dashboard data."
else
  git commit -m "chore: sync dashboard data $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push
  echo "Pushed. Vercel will redeploy automatically."
fi
