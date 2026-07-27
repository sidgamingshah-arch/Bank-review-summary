"""Orchestration RAG: the worker grounds a section on retrieved passages when
RAG is on, falls back to full text per document otherwise, and records retrieval
provenance in the section trace. Driven at the _section_payload seam with
SimpleNamespace (no DB), mirroring test_connectors."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cam.services.orchestration import worker


def _run_job(*, rag_enabled, top_k=3):
    resolution = {
        "sections": [{"section_code": "fa", "order": 1, "prompt": {"payload": {
            "prompt_text": "Analyse {{borrower_name}} cash flow.",
            "uses_industry_kpis": False, "uses_external_context": False}}}],
        "template": {"template_instructions": ""},
        "settings": {"rag_enabled": rag_enabled, "rag_top_k": top_k},
        "kpis": [], "industry_name": "Steel", "global_rules": None, "case": {},
        "connector_context": {},
    }
    run = SimpleNamespace(resolution=resolution, borrower_name="Acme", applied_preferences={})
    job = SimpleNamespace(section_code="fa", fixed_format=False, length_guidance=None,
                          input_docs=[{"doc_id": "d1", "doctype_code": "af", "label": "AF FY25"}])
    return run, job


def _fail_full_text(_id):
    raise AssertionError("full-text fetch must not happen when passages were retrieved")


def test_section_grounds_on_retrieved_passages(monkeypatch):
    # A fact buried deep in a 300-page report (passage #142) is exactly what
    # retrieval surfaces for this section — the whole point of the feature.
    def fake_retrieve(doc_ids, query, top_k):
        assert doc_ids == ["d1"] and "cash flow" in query.lower() and top_k == 3
        return {"query_embedded": True, "results": [{"doc_id": "d1", "chunks": [
            {"ordinal": 142, "text": "Cash flow from operations was Rs 3,120 Cr.", "score": 0.98},
            {"ordinal": 143, "text": "Free cash flow improved to Rs 1,050 Cr.", "score": 0.7}]}]}

    monkeypatch.setattr(worker.resolver, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(worker.resolver, "fetch_document_text", _fail_full_text)

    run, job = _run_job(rag_enabled=True)
    payload = worker._section_payload(run, job)

    doc = payload["grounding_docs"][0]
    assert "Rs 3,120 Cr" in doc["text"] and "passage 142" in doc["text"]
    assert "retrieved passage" in doc["label"]
    prov = payload["retrieval"][0]
    assert prov["doc_id"] == "d1" and prov["fallback"] is False
    assert [p["ordinal"] for p in prov["passages"]] == [142, 143]


def test_section_falls_back_to_full_text_when_nothing_retrieved(monkeypatch):
    monkeypatch.setattr(worker.resolver, "retrieve_chunks",
                        lambda ids, q, k: {"query_embedded": True,
                                           "results": [{"doc_id": "d1", "chunks": []}]})
    monkeypatch.setattr(worker.resolver, "fetch_document_text", lambda d: "FULL DOCUMENT TEXT")

    run, job = _run_job(rag_enabled=True)
    payload = worker._section_payload(run, job)
    assert payload["grounding_docs"][0]["text"] == "FULL DOCUMENT TEXT"
    assert payload["retrieval"][0]["fallback"] is True


def test_retrieval_egress_failure_falls_back(monkeypatch):
    # resolver.retrieve_chunks is fail-open ({} on error) -> full-text grounding
    monkeypatch.setattr(worker.resolver, "retrieve_chunks", lambda ids, q, k: {})
    monkeypatch.setattr(worker.resolver, "fetch_document_text", lambda d: "FULL FALLBACK")

    run, job = _run_job(rag_enabled=True)
    payload = worker._section_payload(run, job)
    assert payload["grounding_docs"][0]["text"] == "FULL FALLBACK"
    assert payload["retrieval"][0]["fallback"] is True


def test_rag_off_uses_full_text_and_never_retrieves(monkeypatch):
    calls = {"retrieve": 0}
    monkeypatch.setattr(worker.resolver, "retrieve_chunks",
                        lambda ids, q, k: calls.__setitem__("retrieve", calls["retrieve"] + 1) or {})
    monkeypatch.setattr(worker.resolver, "fetch_document_text", lambda d: "FULL TEXT ONLY")

    run, job = _run_job(rag_enabled=False)
    payload = worker._section_payload(run, job)
    assert payload["grounding_docs"][0]["text"] == "FULL TEXT ONLY"
    assert payload["retrieval"] == []
    assert calls["retrieve"] == 0


def test_pipeline_records_retrieval_step_in_trace(monkeypatch):
    """The retrieval step lands in the agent trace (auditability) with zero
    token cost and per-document passage provenance."""
    base = {
        "mode": "section",
        "layers": {"global_rules": None, "template_instructions": "",
                   "section_prompt": "Analyse Acme cash flow."},
        "placeholders": {"industry_kpis": ""},
        "grounding_docs": [{"doctype_code": "af", "label": "AF", "text": "x"}],
        "preferences": None, "fixed_format": False, "length_guidance": None,
        "model_overrides": None,
        "retrieval": [{"doc_id": "d1", "label": "AF", "fallback": False,
                       "passages": [{"ordinal": 5, "score": 0.9}]}],
    }
    monkeypatch.setattr(worker, "_section_payload", lambda run, job: base)
    monkeypatch.setattr(worker.resolver, "genai_extract",
                        lambda p: {"facts": [], "parse_ok": True, "model": "m", "usage": {}})
    monkeypatch.setattr(worker.resolver, "genai_generate",
                        lambda p: {"content": "draft", "model": "m", "usage": {},
                                   "untraceable_numbers": []})
    # checks disabled so the pipeline needs no DB (no cross-section digest)
    run = SimpleNamespace(id="run-x", resolution={
        "settings": {"agents_materiality_enabled": False, "agents_consistency_enabled": False},
        "agent_rules": {}})
    job = SimpleNamespace(section_code="fa", fixed_format=False, length_guidance=None,
                          input_docs=[], kind="initial")

    result = worker._run_agent_pipeline(run, job)
    step = next(t for t in result["trace"] if t["agent"] == "retrieval")
    assert step["tokens_in"] == 0 and step["tokens_out"] == 0
    assert step["passages"] == 1
    assert step["retrieval"][0]["passages"][0]["ordinal"] == 5
