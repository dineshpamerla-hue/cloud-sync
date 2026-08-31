"""End-to-end test of the sync loop using a real LocalSource and a fake
in-memory Destination — no rclone/network needed, so this runs in CI.
"""
import tempfile
from pathlib import Path

import pytest

from cloud_sync.config import JobConfig, SourceConfig, DestinationConfig
from cloud_sync.engine.manifest import Manifest
from cloud_sync.engine import uploader as uploader_mod


class FakeDestination:
    """Records uploads in memory instead of talking to B2."""

    def __init__(self, **kwargs):
        self.uploaded = {}
        self.fail_next = set()

    def describe(self):
        return "fake"

    def exists(self, key):
        return key in self.uploaded

    def upload_file(self, local_path: Path, key: str):
        if key in self.fail_next:
            self.fail_next.discard(key)
            raise RuntimeError("simulated transient failure")
        self.uploaded[key] = local_path.read_bytes()


@pytest.fixture
def workspace(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        source_dir = tmp / "pictures"
        source_dir.mkdir()
        (source_dir / "a.jpg").write_bytes(b"aaa")
        (source_dir / "b.jpg").write_bytes(b"bbb")

        monkeypatch.setenv("CLOUD_SYNC_HOME", str(tmp / "home"))

        fake_dest = FakeDestination()
        monkeypatch.setattr(uploader_mod, "build_destination", lambda type_, options: fake_dest)

        job = JobConfig(
            name="test-job",
            source=SourceConfig(type="local", options={"path": str(source_dir)}),
            destination=DestinationConfig(type="fake", options={"bucket": "test-bucket", "prefix_strategy": "mirror"}),
        )
        manifest = Manifest(tmp / "home" / "manifest.db")
        yield job, manifest, fake_dest, source_dir
        manifest.close()


def test_first_run_uploads_everything(workspace):
    job, manifest, fake_dest, source_dir = workspace
    stats = uploader_mod.run_job(job, manifest=manifest)

    assert stats.files_total == 2
    assert stats.files_uploaded == 2
    assert stats.files_skipped == 0
    assert set(fake_dest.uploaded) == {"a.jpg", "b.jpg"}


def test_second_run_skips_unchanged_files(workspace):
    job, manifest, fake_dest, source_dir = workspace
    uploader_mod.run_job(job, manifest=manifest)

    stats = uploader_mod.run_job(job, manifest=manifest)
    assert stats.files_uploaded == 0
    assert stats.files_skipped == 2


def test_modified_file_is_reuploaded(workspace):
    job, manifest, fake_dest, source_dir = workspace
    uploader_mod.run_job(job, manifest=manifest)

    (source_dir / "a.jpg").write_bytes(b"aaa-modified-longer")
    stats = uploader_mod.run_job(job, manifest=manifest)

    assert stats.files_uploaded == 1
    assert stats.files_skipped == 1
    assert fake_dest.uploaded["a.jpg"] == b"aaa-modified-longer"


def test_new_file_added_between_runs_is_picked_up(workspace):
    job, manifest, fake_dest, source_dir = workspace
    uploader_mod.run_job(job, manifest=manifest)

    (source_dir / "c.jpg").write_bytes(b"ccc")
    stats = uploader_mod.run_job(job, manifest=manifest)

    assert stats.files_uploaded == 1
    assert stats.files_skipped == 2
    assert "c.jpg" in fake_dest.uploaded


def test_dry_run_does_not_upload(workspace):
    job, manifest, fake_dest, source_dir = workspace
    stats = uploader_mod.run_job(job, manifest=manifest, dry_run=True)

    assert stats.files_uploaded == 2  # counted as "would upload"
    assert fake_dest.uploaded == {}


def test_skip_only_run_persists_counts_to_run_record(workspace):
    """A resumed run that skips every file must still record its counts in the
    runs table (the dashboard/status/export-history read from there), not leave
    the row at its 0 defaults.
    """
    job, manifest, fake_dest, source_dir = workspace
    uploader_mod.run_job(job, manifest=manifest)   # first run uploads both

    uploader_mod.run_job(job, manifest=manifest)   # second run skips both

    last = manifest.recent_runs(job.name, limit=1)[0]
    assert last.files_total == 2
    assert last.files_skipped == 2
    assert last.files_uploaded == 0
