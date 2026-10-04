from __future__ import annotations

from backend import llm


def test_llm_client_honors_litellm_base_url(monkeypatch):
    llm.reset_llm_client_cache()
    monkeypatch.setattr("backend.config.LLM_API_KEY", "proxy-key")
    monkeypatch.setattr("backend.config.LLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setattr("backend.config.LLM_TIMEOUT_SECONDS", 12.5)

    client = llm.get_llm_client()

    assert client is not None
    assert str(client.base_url).rstrip("/") == "http://localhost:4000"


def test_llm_requires_proxy_key_and_base_url(monkeypatch):
    llm.reset_llm_client_cache()
    monkeypatch.setattr("backend.config.LLM_API_KEY", "")
    monkeypatch.setattr("backend.config.LLM_BASE_URL", "")

    assert llm.resolve_llm_api_key() == ""
    assert llm.resolve_llm_base_url() == ""
    assert llm.llm_is_configured() is False
    assert llm.get_llm_client() is None


def test_runtime_models_are_task_specific(monkeypatch):
    monkeypatch.setattr("backend.config.SUGGESTION_MODEL", "suggest-smart")
    monkeypatch.setattr("backend.config.EXTRACTION_MODEL", "extract-json")
    monkeypatch.setattr("backend.config.LLM_API_KEY", "proxy-key")
    monkeypatch.setattr("backend.config.LLM_BASE_URL", "http://localhost:4000")

    runtime = llm.llm_runtime_config()

    assert runtime["suggestion_model"] == "suggest-smart"
    assert runtime["extraction_model"] == "extract-json"
    assert runtime["llm_configured"] is True
