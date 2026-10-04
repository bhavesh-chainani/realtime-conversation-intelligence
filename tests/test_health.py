"""Smoke tests for the health and session endpoints."""

from __future__ import annotations


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_ready_reports_each_component(client):
    body = client.get("/ready").json()
    assert set(body) >= {
        "ready",
        "llm_configured",
        "transcription_configured",
        "diarization",
        "customer_db_configured",
    }
    assert "ready" in body["diarization"]


def test_stt_session_issues_relay_ticket(client, monkeypatch):
    monkeypatch.setattr("backend.config.ASSEMBLYAI_API_KEY", "key")
    body = client.get("/stt/session").json()
    assert body["path"] == "/ws/stt" and body["ticket"]


def test_stt_session_needs_assemblyai_key(client, monkeypatch):
    monkeypatch.setattr("backend.config.ASSEMBLYAI_API_KEY", "")
    assert client.get("/stt/session").status_code == 503
