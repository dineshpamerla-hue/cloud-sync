#!/usr/bin/env python3
"""Stop hook: nudge to update CLAUDE.md + /compact once the session gets large.

Claude Code runs this after each turn (a `Stop` hook). It reads the hook JSON
from stdin, estimates the transcript size in tokens (~4 bytes/token), and — if
the session has crossed a threshold — prints a one-line reminder via the
`systemMessage` field. A hook cannot run `/compact` itself; this is only a
nudge for the human to update CLAUDE.md and compact when it's worth it.

De-dupes per session via a marker file in the system temp dir so it doesn't
warn every single turn: it re-warns only after the estimate grows another
~50% past the last time it fired.

Threshold override: env CLOUD_SYNC_SIZE_REMINDER_TOKENS (default 100000).
Never blocks the turn — always exits 0, even on unexpected errors.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

DEFAULT_THRESHOLD_TOKENS = 100_000
BYTES_PER_TOKEN = 4  # rough heuristic; good enough for a "getting big" signal
REWARN_GROWTH = 1.5  # re-warn only after tokens grow 50% past last warning


def _threshold() -> int:
    raw = os.environ.get("CLOUD_SYNC_SIZE_REMINDER_TOKENS")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_THRESHOLD_TOKENS


def _marker_path(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"cloud-sync-size-reminder-{digest}")


def _last_warned_tokens(marker: str) -> int:
    try:
        with open(marker) as fh:
            return int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def _record_warned(marker: str, tokens: int) -> None:
    try:
        with open(marker, "w") as fh:
            fh.write(str(tokens))
    except OSError:
        pass  # non-fatal: worst case we warn again next turn


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # not our data to interpret; stay silent

    transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id") or "unknown-session"
    if not transcript_path or not os.path.exists(transcript_path):
        return 0

    try:
        size_bytes = os.path.getsize(transcript_path)
    except OSError:
        return 0

    est_tokens = size_bytes // BYTES_PER_TOKEN
    threshold = _threshold()
    if est_tokens < threshold:
        return 0

    marker = _marker_path(session_id)
    last = _last_warned_tokens(marker)
    if last and est_tokens < last * REWARN_GROWTH:
        return 0  # already warned recently; wait for meaningful growth

    _record_warned(marker, est_tokens)
    approx_k = round(est_tokens / 1000)
    message = (
        f"Session is ~{approx_k}k tokens (transcript {size_bytes // 1024} KB). "
        "Update CLAUDE.md with anything worth persisting for next time, then "
        "run /compact to reclaim context."
    )
    print(json.dumps({"systemMessage": message}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
