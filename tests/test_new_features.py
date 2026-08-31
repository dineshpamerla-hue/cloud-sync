"""Tests for the manifest-loss existence fallback, `cloud-sync export-history`,
and the session-size reminder hook — all runnable in CI (no network/rclone).
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cloud_sync.config import JobConfig, SourceConfig, DestinationConfig
from cloud_sync.engine.manifest import Manifest
from cloud_sync.engine import uploader as uploader_mod


class FakeDestinationWithListing:
    """Fake destination that also knows what keys already exist remotely,
    exercising the manifest-loss recovery path in the uploader.
    """

    def __init__(self, existing=None, **kwargs):
        self.uploaded = {}
        self._existing = set(existing or [])
        self.list_calls = 0

    def describe(self):
        return "fake-listing"

    def exists(self, key):
        return key in self.uploaded or key in self._existing

    def upload_file(self, local_path: Path, key: str):
        self.uploaded[key] = local_path.read_bytes()

    def list_existing_keys(self, prefix: str = ""):
        self.list_calls += 1
        return set(self._existing)


@pytest.fixture
def workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source_dir = tmp / "pics"
        source_dir.mkdir()
        (source_dir / "a.jpg").write_bytes(b"aaa")
        (source_dir / "b.jpg").write_bytes(b"bbb")
        monkeypatch.setenv("CLOUD_SYNC_HOME", str(tmp / "home"))

        job = JobConfig(
            name="test-job",
            source=SourceConfig(type="local", options={"path": str(source_dir)}),
            destination=DestinationConfig(type="fake", options={"bucket": "b", "prefix_strategy": "mirror"}),
        )
        manifest = Manifest(tmp / "home" / "manifest.db")
        yield job, manifest, source_dir, tmp
        manifest.close()


def test_existence_fallback_skips_already_uploaded_keys(workspace, monkeypatch):
    """With an empty manifest, keys already present at the destination are
    recorded as done (skipped) instead of re-uploaded.
    """
    job, manifest, source_dir, tmp = workspace
    fake = FakeDestinationWithListing(existing={"a.jpg"})
    monkeypatch.setattr(uploader_mod, "build_destination", lambda t, o: fake)

    stats = uploader_mod.run_job(job, manifest=manifest)

    assert fake.list_calls == 1
    assert "a.jpg" not in fake.uploaded   # not re-uploaded
    assert fake.uploaded.get("b.jpg") == b"bbb"  # genuinely new, uploaded
    assert stats.files_uploaded == 1
    assert stats.files_skipped == 1
    # a.jpg is now recorded, so a second run skips it via the manifest.
    assert manifest.get_file("test-job", "a.jpg").status == "uploaded"


def test_existence_listing_skipped_when_manifest_has_state(workspace, monkeypatch):
    """The (potentially expensive) listing is only primed on an empty manifest."""
    job, manifest, source_dir, tmp = workspace
    fake = FakeDestinationWithListing(existing=set())
    monkeypatch.setattr(uploader_mod, "build_destination", lambda t, o: fake)

    uploader_mod.run_job(job, manifest=manifest)   # populates the manifest
    fake.list_calls = 0
    uploader_mod.run_job(job, manifest=manifest)   # second run

    assert fake.list_calls == 0


def test_export_history_has_runs_but_no_filenames(workspace, monkeypatch):
    job, manifest, source_dir, tmp = workspace
    fake = FakeDestinationWithListing(existing=set())
    monkeypatch.setattr(uploader_mod, "build_destination", lambda t, o: fake)
    uploader_mod.run_job(job, manifest=manifest)

    # Write a one-job config the CLI can load.
    cfg = tmp / "jobs.yaml"
    cfg.write_text(
        "jobs:\n"
        "  - name: test-job\n"
        "    source:\n      type: local\n      path: " + str(source_dir) + "\n"
        "    destination:\n      type: fake\n      bucket: b\n"
    )
    out = tmp / "history.json"
    result = subprocess.run(
        [sys.executable, "-m", "cloud_sync.cli", "export-history",
         "--config", str(cfg), "--out", str(out)],
        capture_output=True, text=True,
        env={**_env(tmp)},
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(out.read_text())
    assert data["jobs"][0]["name"] == "test-job"
    assert len(data["runs"]) >= 1
    # No source filenames should appear anywhere in the snapshot.
    blob = json.dumps(data)
    assert "a.jpg" not in blob and "b.jpg" not in blob


def _env(tmp: Path):
    import os
    e = dict(os.environ)
    e["CLOUD_SYNC_HOME"] = str(tmp / "home")
    e["PYTHONPATH"] = str(Path(__file__).parent.parent / "src")
    return e


# --- session-size reminder hook -------------------------------------------

HOOK = Path(__file__).parent.parent / ".claude" / "hooks" / "size-reminder.py"


def _run_hook(transcript: Path, session_id: str, threshold=None):
    import os
    env = dict(os.environ)
    if threshold is not None:
        env["CLOUD_SYNC_SIZE_REMINDER_TOKENS"] = str(threshold)
    payload = json.dumps({"transcript_path": str(transcript), "session_id": session_id})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


def test_size_hook_warns_over_threshold_then_stays_quiet(tmp_path):
    import uuid
    big = tmp_path / "t.jsonl"
    big.write_text("x" * 500_000)   # ~125k tokens
    sess = "hook-sess-A-" + uuid.uuid4().hex   # unique so the marker never pre-exists

    r1 = _run_hook(big, sess, threshold=100_000)
    assert r1.returncode == 0
    assert "systemMessage" in r1.stdout   # warned

    r2 = _run_hook(big, sess, threshold=100_000)
    assert r2.returncode == 0
    assert r2.stdout.strip() == ""        # deduped, silent


def test_size_hook_silent_under_threshold(tmp_path):
    small = tmp_path / "s.jsonl"
    small.write_text("x" * 1000)
    r = _run_hook(small, "hook-sess-B", threshold=100_000)
    assert r.returncode == 0
    assert r.stdout.strip() == ""


def test_size_hook_survives_missing_transcript(tmp_path):
    r = _run_hook(tmp_path / "does-not-exist.jsonl", "hook-sess-C")
    assert r.returncode == 0
    assert r.stdout.strip() == ""


# --- _RcloneCatStream subprocess reaping ----------------------------------

def _cat_stream(code: str):
    from cloud_sync.sources.google_drive import _RcloneCatStream
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return _RcloneCatStream(proc)


def test_rclone_cat_stream_raises_on_nonzero_exit():
    """A `cat` that emits some bytes then exits non-zero must raise on close —
    otherwise a partial/failed transfer looks like a short-but-successful read
    and we'd silently hash/upload a truncated file.
    """
    stream = _cat_stream(
        "import sys; sys.stdout.buffer.write(b'partial'); "
        "sys.stderr.write('boom'); sys.exit(3)"
    )
    with pytest.raises(RuntimeError, match="rclone cat failed"):
        with stream as s:
            while s.read(1024):
                pass


def test_rclone_cat_stream_ok_on_clean_exit():
    stream = _cat_stream("import sys; sys.stdout.buffer.write(b'hello')")
    with stream as s:
        data = b""
        while chunk := s.read(1024):
            data += chunk
    assert data == b"hello"
