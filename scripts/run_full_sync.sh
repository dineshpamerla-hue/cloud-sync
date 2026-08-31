#!/usr/bin/env bash
# Convenience wrapper for cron/manual full runs: runs every job in
# config/jobs.yaml back-to-back, then verifies each one.
# Usage: scripts/run_full_sync.sh [--skip-verify]
set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_VERIFY=false
[[ "${1:-}" == "--skip-verify" ]] && SKIP_VERIFY=true

JOBS=$(python3 -c "
import yaml
with open('config/jobs.yaml') as f:
    print(' '.join(j['name'] for j in yaml.safe_load(f)['jobs']))
")

for job in $JOBS; do
  echo "=== running $job ==="
  cloud-sync run --job "$job"
  if [[ "$SKIP_VERIFY" == "false" ]]; then
    echo "=== verifying $job ==="
    cloud-sync verify --job "$job" --sample 200 || echo "WARNING: verify found mismatches for $job — check before deleting source data."
  fi
done

echo "=== syncing dashboard data ==="
"$(dirname "$0")/sync_dashboard_data.sh" || echo "WARNING: dashboard data sync failed (non-fatal, local run history is still in ~/.cloud-sync)."
