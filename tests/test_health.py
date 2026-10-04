"""Smoke tests for the health endpoints."""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_ready(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ready") is True
    assert "llm_configured" in body
    assert "assemblyai_configured" in body
    assert "diarization" in body
