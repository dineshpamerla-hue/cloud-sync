from datetime import datetime, timezone

import pytest

from cloud_sync.engine.organizer import build_key, date_prefixed_key, mirror_key


def test_date_prefixed_key_uses_year_month():
    when = datetime(2023, 7, 4, tzinfo=timezone.utc)
    assert date_prefixed_key("IMG_1234.jpg", when) == "2023/07/IMG_1234.jpg"


def test_date_prefixed_key_nested_path_flattens_to_filename():
    when = datetime(2023, 7, 4, tzinfo=timezone.utc)
    assert date_prefixed_key("2023/vacation/IMG_1234.jpg", when) == "2023/07/IMG_1234.jpg"


def test_date_prefixed_key_falls_back_when_no_date():
    assert date_prefixed_key("mystery.jpg", None) == "unknown-date/mystery.jpg"


def test_mirror_key_preserves_structure():
    assert mirror_key("Work/Reports/q1.pdf") == "Work/Reports/q1.pdf"


def test_build_key_dispatches_on_strategy():
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert build_key("date", "a.jpg", when) == "2024/01/a.jpg"
    assert build_key("mirror", "a/b/c.pdf") == "a/b/c.pdf"


def test_build_key_rejects_unknown_strategy():
    with pytest.raises(ValueError):
        build_key("nonsense", "a.jpg")
