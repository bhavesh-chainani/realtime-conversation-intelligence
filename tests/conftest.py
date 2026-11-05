"""Pytest configuration."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api import app


@pytest.fixture
def client():
    return TestClient(app)
