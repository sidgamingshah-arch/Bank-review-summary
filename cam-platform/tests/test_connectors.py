"""External grounding connectors: fetch (mock + fail-open), the settings
plumbing (toggles + read-only LLM view), and the worker's gated injection of
connector context into a section's extraction grounding."""
from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from cam.services.master_config.main import app as mc_app, engine as mc_engine
from cam.services.master_config.models import Base as McBase
from cam.services.orchestration import resolver, worker

McBase.metadata.create_all(mc_engine)  # order-independent: ensure tables exist
mc = TestClient(mc_app)


def test_connector_mock_shapes(monkeypatch):
    # no URL configured -> deterministic, clearly-labelled mock feed
    monkeypatch.setattr(resolver.settings, "connector_news_url", "")
    monkeypatch.setattr(resolver.settings, "connector_search_url", "")
    news = resolver.fetch_connector_context("news", "Acme Steel", "Steel")
    web = resolver.fetch_connector_context("search", "Acme Steel", "Steel")
    assert news and news[0]["doctype_code"] == "external_news"
    assert news[0]["label"].startswith("NEWS:")
    assert web and web[0]["doctype_code"] == "external_web"
    assert web[0]["label"].startswith("SEARCH:")


def test_connector_failopen_on_http_error(monkeypatch):
    monkeypatch.setattr(resolver.settings, "connector_news_url", "https://feeds.internal/news")

    def boom(*a, **k):
        raise RuntimeError("connector down")

    monkeypatch.setattr(resolver, "gateway_client", boom)
    # never raises; returns [] so the run proceeds on documents alone
    assert resolver.fetch_connector_context("news", "Acme", "Steel") == []


def test_settings_expose_connectors_and_llm(admin_headers):
    s = mc.get("/api/masters/settings", headers=admin_headers).json()
    assert s["connectors_news_enabled"] is False
    assert s["connectors_search_enabled"] is False
    assert "_llm" in s and set(s["_llm"]) >= {"provider", "model", "api_key_configured"}

    up = mc.put("/api/masters/settings", headers=admin_headers,
                json={"connectors_news_enabled": True}).json()
    assert up["connectors_news_enabled"] is True
    assert "_llm" in up  # read-only block returned on PUT too
    # reset so other tests see the default
    mc.put("/api/masters/settings", headers=admin_headers, json={"connectors_news_enabled": False})


def _run_and_job(uses_external, news_on):
    resolution = {
        "sections": [{"section_code": "s1", "order": 1,
                      "prompt": {"payload": {"prompt_text": "Assess {{borrower_name}}.",
                                             "uses_industry_kpis": False,
                                             "uses_external_context": uses_external}}}],
        "template": {"template_instructions": ""},
        "settings": {"connectors_news_enabled": news_on, "connectors_search_enabled": False},
        "kpis": [], "industry_name": "Steel", "global_rules": None, "case": {},
    }
    run = SimpleNamespace(resolution=resolution, borrower_name="Acme", applied_preferences={})
    job = SimpleNamespace(section_code="s1", fixed_format=False, length_guidance=None,
                          input_docs=[{"doc_id": "d1", "doctype_code": "af", "label": "AF"}])
    return run, job


def test_worker_injects_connector_grounding_when_opted_in(monkeypatch):
    monkeypatch.setattr(worker.resolver, "fetch_document_text", lambda _id: "Revenue 100 crore.")
    calls = {}

    def fake_conn(kind, borrower, industry, max_items=None):
        calls[kind] = (borrower, industry)
        return [{"doctype_code": "external_news", "label": "NEWS:X", "text": "No adverse news."}]

    monkeypatch.setattr(worker.resolver, "fetch_connector_context", fake_conn)

    run, job = _run_and_job(uses_external=True, news_on=True)
    payload = worker._section_payload(run, job)
    labels = [d["label"] for d in payload["grounding_docs"]]
    assert "NEWS:X" in labels and "AF" in labels
    assert calls.get("news") == ("Acme", "Steel")
    assert "search" not in calls  # search toggle off -> not fetched


def test_worker_skips_connector_when_not_opted_in(monkeypatch):
    monkeypatch.setattr(worker.resolver, "fetch_document_text", lambda _id: "Revenue 100 crore.")
    calls = {}
    monkeypatch.setattr(worker.resolver, "fetch_connector_context",
                        lambda *a, **k: calls.setdefault("hit", True) or [])

    # section does not opt in -> connector never consulted even when enabled
    run, job = _run_and_job(uses_external=False, news_on=True)
    payload = worker._section_payload(run, job)
    assert calls == {}
    assert [d["label"] for d in payload["grounding_docs"]] == ["AF"]


def test_gap_trailer_discloses_external_sources():
    run = SimpleNamespace(gaps=[])
    sec = SimpleNamespace(
        name="Industry & Market Analysis", status="complete", skip_reason=None,
        error=None, untraceable=[], checks={},
        facts=[{"source": "NEWS:MOCK-NEWS recent", "value": "12", "quote": "...12 months..."}])
    trailer = worker.build_gap_trailer(run, [sec])
    assert "External intelligence consulted" in trailer
    assert "NEWS:MOCK-NEWS recent" in trailer
