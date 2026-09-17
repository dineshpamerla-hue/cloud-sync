"""Tests for GoogleDriveSource remote handling, including scoping a job to a
subfolder (`gdrive:pics`) rather than the whole drive.
"""
import pytest

from cloud_sync.sources.google_drive import GoogleDriveSource


@pytest.fixture
def rclone_on_path(monkeypatch):
    monkeypatch.setattr(
        "cloud_sync.sources.google_drive.shutil.which", lambda _: "/usr/bin/rclone"
    )


def test_bare_remote_gets_colon(rclone_on_path):
    assert GoogleDriveSource("gdrive").remote == "gdrive:"


def test_drive_root_joins_without_separator(rclone_on_path):
    source = GoogleDriveSource("gdrive:")
    assert source.remote_path("a/b.jpg") == "gdrive:a/b.jpg"


def test_subfolder_remote_is_preserved(rclone_on_path):
    source = GoogleDriveSource("gdrive:pics")
    assert source.remote == "gdrive:pics"
    assert source.remote_path("2024/x.jpg") == "gdrive:pics/2024/x.jpg"


def test_trailing_slash_does_not_double_up(rclone_on_path):
    source = GoogleDriveSource("gdrive:pics/")
    assert source.remote_path("x.jpg") == "gdrive:pics/x.jpg"
