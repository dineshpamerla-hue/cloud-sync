"""The dashboard's CORS allowlist is built at import time from Vercel's own
injected host vars, so it needs a module reload to exercise.
"""
import importlib

import pytest

import cloud_sync.web.api as api_mod


@pytest.fixture
def reload_api(monkeypatch):
    def _reload(**env):
        for var in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL", "DASHBOARD_ALLOWED_ORIGINS"):
            monkeypatch.delenv(var, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(api_mod)

    yield _reload
    importlib.reload(api_mod)


def test_localhost_always_allowed(reload_api):
    assert "http://localhost:8000" in reload_api()._allowed_origins


def test_vercel_url_becomes_https_origin(reload_api):
    origins = reload_api(VERCEL_URL="cloud-sync-abc123.vercel.app")._allowed_origins
    assert "https://cloud-sync-abc123.vercel.app" in origins


def test_production_url_and_deployment_url_both_allowed(reload_api):
    origins = reload_api(
        VERCEL_PROJECT_PRODUCTION_URL="cloud-sync.vercel.app",
        VERCEL_URL="cloud-sync-abc123.vercel.app",
    )._allowed_origins
    assert "https://cloud-sync.vercel.app" in origins
    assert "https://cloud-sync-abc123.vercel.app" in origins


def test_extra_origins_are_split_and_trimmed(reload_api):
    origins = reload_api(
        DASHBOARD_ALLOWED_ORIGINS="https://a.example.com/, https://b.example.com"
    )._allowed_origins
    assert "https://a.example.com" in origins
    assert "https://b.example.com" in origins


def test_wildcard_is_never_allowed(reload_api):
    assert "*" not in reload_api(VERCEL_URL="cloud-sync.vercel.app")._allowed_origins
