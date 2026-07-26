"""Editable LLM egress config: master-config storage/validation (key never
accepted) and the genai gateway building an effective provider from env
overlaid with the admin overrides, reloadable without a restart."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import cam.services.genai.main as genai
from cam.services.genai import providers
from cam.services.master_config.main import app as mc_app, engine as mc_engine
from cam.services.master_config.models import Base as McBase

McBase.metadata.create_all(mc_engine)
mc = TestClient(mc_app)


@pytest.fixture(autouse=True)
def _reset_genai_globals():
    # genai holds the provider + overrides as module globals; reset after each
    # test so a live provider never leaks into another test's mock expectations.
    yield
    genai._provider = None
    genai._overrides = None


# ---- master-config storage + validation ----------------------------------

def test_llm_config_put_get_roundtrip(admin_headers):
    r = mc.put("/api/masters/llm-config", headers=admin_headers, json={
        "llm_provider": "openai", "genai_base_url": "https://llm.example/v1",
        "genai_model": "m1", "genai_temperature": 0.2})
    assert r.status_code == 200, r.text
    cfg = mc.get("/api/masters/llm-config", headers=admin_headers).json()
    assert cfg["llm_provider"] == "openai"
    assert cfg["genai_base_url"] == "https://llm.example/v1"
    assert cfg["genai_model"] == "m1" and cfg["genai_temperature"] == 0.2


def test_llm_config_openai_requires_base_url(admin_headers):
    r = mc.put("/api/masters/llm-config", headers=admin_headers,
               json={"llm_provider": "openai", "genai_base_url": ""})
    assert r.status_code == 422


def test_llm_config_rejects_bad_base_url(admin_headers):
    r = mc.put("/api/masters/llm-config", headers=admin_headers,
               json={"llm_provider": "openai", "genai_base_url": "ftp://nope"})
    assert r.status_code == 422


def test_llm_config_never_accepts_a_key_field(admin_headers):
    # a stray "api_key"/"genai_api_key" is ignored by the whitelist model, never stored
    r = mc.put("/api/masters/llm-config", headers=admin_headers,
               json={"llm_provider": "mock", "api_key": "sk-secret", "genai_api_key": "sk-secret"})
    assert r.status_code == 200
    cfg = mc.get("/api/masters/llm-config", headers=admin_headers).json()
    assert "api_key" not in cfg and "genai_api_key" not in cfg


def test_llm_config_put_requires_settings_capability(analyst_headers):
    r = mc.put("/api/masters/llm-config", headers=analyst_headers, json={"llm_provider": "mock"})
    assert r.status_code == 403


# ---- genai effective provider + reload ------------------------------------

def test_effective_settings_overlay_and_provider(monkeypatch):
    monkeypatch.setattr(genai, "_load_overrides", lambda: {
        "llm_provider": "openai", "genai_base_url": "https://llm.example/v1", "genai_model": "m9"})
    genai._overrides = None
    genai._provider = None
    eff = genai._effective_settings()
    assert eff.llm_provider == "openai" and eff.genai_model == "m9"
    assert eff.genai_base_url == "https://llm.example/v1"
    assert isinstance(genai.get_provider(), providers.OpenAICompatibleProvider)


def test_config_endpoint_reflects_overrides(monkeypatch, service_headers):
    monkeypatch.setattr(genai, "_load_overrides",
                        lambda: {"llm_provider": "anthropic", "genai_model": "claude-x"})
    genai._overrides = None
    genai._provider = None
    with TestClient(genai.app) as c:
        cfg = c.get("/api/genai/config", headers=service_headers).json()
    assert cfg["provider"] == "anthropic" and cfg["model"] == "claude-x"
    assert {"temperature", "timeout_seconds", "auth_scheme"} <= set(cfg)


def test_reload_endpoint_is_service_only(monkeypatch, service_headers):
    monkeypatch.setattr(genai, "_load_overrides", lambda: {})
    with TestClient(genai.app) as c:
        assert c.post("/api/genai/reload").status_code in (401, 403)
        r = c.post("/api/genai/reload", headers=service_headers)
    assert r.status_code == 200 and r.json()["reloaded"] is True
