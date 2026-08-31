import io
import tempfile
from pathlib import Path

import pytest

from cloud_sync.engine.manifest import Manifest, hash_file


@pytest.fixture
def manifest():
    with tempfile.TemporaryDirectory() as tmp:
        m = Manifest(Path(tmp) / "manifest.db")
        yield m
        m.close()


def test_hash_file_is_deterministic():
    h1 = hash_file(io.BytesIO(b"hello world"))
    h2 = hash_file(io.BytesIO(b"hello world"))
    assert h1 == h2
    assert h1 != hash_file(io.BytesIO(b"different"))


def test_needs_upload_true_for_unseen_file(manifest):
    assert manifest.needs_upload("job-a", "photo.jpg", 1234) is True


def test_needs_upload_false_after_recording_success(manifest):
    manifest.record_result("job-a", "photo.jpg", "2024/01/photo.jpg", "uploaded",
                            content_hash="abc123", size_bytes=1234)
    assert manifest.needs_upload("job-a", "photo.jpg", 1234) is False


def test_needs_upload_true_if_size_changed(manifest):
    manifest.record_result("job-a", "photo.jpg", "2024/01/photo.jpg", "uploaded",
                            content_hash="abc123", size_bytes=1234)
    assert manifest.needs_upload("job-a", "photo.jpg", 9999) is True


def test_needs_upload_true_after_failure(manifest):
    manifest.record_result("job-a", "photo.jpg", "2024/01/photo.jpg", "failed",
                            size_bytes=1234, error="boom")
    assert manifest.needs_upload("job-a", "photo.jpg", 1234) is True


def test_jobs_do_not_share_state(manifest):
    manifest.record_result("job-a", "photo.jpg", "key", "uploaded", size_bytes=10)
    assert manifest.needs_upload("job-b", "photo.jpg", 10) is True


def test_run_lifecycle_tracks_counts(manifest):
    run_id = manifest.start_run("job-a")
    manifest.update_run_counts(run_id, files_total=10, files_uploaded=8,
                                files_skipped=1, files_failed=1, bytes_uploaded=5000)
    manifest.finish_run(run_id, "completed_with_errors")

    run = manifest.get_run(run_id)
    assert run.files_uploaded == 8
    assert run.files_failed == 1
    assert run.status == "completed_with_errors"
    assert run.finished_at is not None


def test_recent_runs_ordered_newest_first(manifest):
    id1 = manifest.start_run("job-a")
    manifest.finish_run(id1, "completed")
    id2 = manifest.start_run("job-a")
    manifest.finish_run(id2, "completed")

    runs = manifest.recent_runs("job-a")
    assert runs[0].id == id2
    assert runs[1].id == id1
