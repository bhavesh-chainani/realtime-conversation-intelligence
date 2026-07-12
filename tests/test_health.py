"""Smoke tests for public ops endpoints."""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert "X-Request-ID" in r.headers


def test_ready_lax(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ready") is True
    assert "openai_configured" in body
    assert "assemblyai_configured" in body


def test_ready_strict_missing_keys_returns_503(client, monkeypatch):
    monkeypatch.setattr("backend.config.OPENAI_API_KEY", "")
    monkeypatch.setattr("backend.config.ASSEMBLYAI_API_KEY", "")
    monkeypatch.setattr("backend.config.STRICT_READINESS", True)
    r = client.get("/ready")
    assert r.status_code == 503


def test_metrics_disabled_returns_404(client, monkeypatch):
    monkeypatch.setattr("backend.config.METRICS_ENABLED", False)
    r = client.get("/metrics")
    assert r.status_code == 404
