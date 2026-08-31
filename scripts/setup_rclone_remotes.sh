#!/usr/bin/env bash
# One-time interactive setup for the rclone remotes cloud-sync depends on.
# Safe to re-run — rclone will just let you reconfigure an existing remote.
set -euo pipefail

if ! command -v rclone &> /dev/null; then
  echo "rclone not found. Install it first:  brew install rclone"
  exit 1
fi

echo "== Backblaze B2 remote ('backblaze:') =="
if rclone listremotes | grep -q '^backblaze:$'; then
  echo "Remote 'backblaze:' already configured. Skipping (run 'rclone config' manually to edit it)."
else
  echo "You'll need your B2 keyID and applicationKey (same values as B2_KEY_ID / B2_APPLICATION_KEY in .env)."
  rclone config create backblaze b2 account "$B2_KEY_ID" key "$B2_APPLICATION_KEY"
fi

echo
echo "== Google Drive remote ('gdrive:') =="
if rclone listremotes | grep -q '^gdrive:$'; then
  echo "Remote 'gdrive:' already configured. Skipping."
else
  echo "This will open a browser window for Google OAuth."
  rclone config create gdrive drive
fi

echo
echo "Done. Verify with:"
echo "  rclone lsd backblaze:"
echo "  rclone lsd gdrive:"
