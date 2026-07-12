"""Pytest configuration: stable env before app import."""

from __future__ import annotations

import os

# Import-time settings in backend.config — keep CI/tests deterministic.
os.environ.setdefault("METRICS_ENABLED", "false")
os.environ.setdefault("REQUIRE_API_AUTH", "false")
os.environ.setdefault("ASYNC_JOBS_ENABLED", "false")
os.environ.setdefault("STRICT_READINESS", "false")

import pytest
from fastapi.testclient import TestClient

from backend.api import app


@pytest.fixture
def client():
    return TestClient(app)
