"""Document-service RAG: chunk+embed at intake (gated + fail-open), the
/retrieve ranking endpoint, and on-demand /reindex."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_service_headers, make_user_headers

from cam.services.document import embedding as doc_embedding
from cam.services.document import main as doc_main
from cam.services.document import vaf

ANALYST1 = make_user_headers("analyst1", ["analyst"])
SERVICE = make_service_headers("orchestration")

# A two-paragraph doc: one paragraph about cash flow, one about revenue, so a
# small chunk size splits them and retrieval can pick the relevant one.
DOC_BODY = ("Revenue rose strongly this fiscal year for the whole group.\n\n"
            "Cash flow from operations improved and liquidity remained ample.\n\n"
            "Governance and board composition are described in the final part.")


def _fake_embed(texts):
    """Deterministic 3-dim vectors keyed on topic markers so ranking is testable
    without a model."""
    out = []
    for t in texts:
        tl = t.lower()
        out.append([1.0 if "cash flow" in tl else 0.0,
                    1.0 if "revenue" in tl else 0.0,
                    0.05])
    return out


@pytest.fixture()
def client():
    with TestClient(doc_main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_auto_tag(monkeypatch):
    monkeypatch.setattr(vaf, "classify_document", lambda filename, text: None)


@pytest.fixture()
def rag_on(monkeypatch):
    monkeypatch.setattr(vaf, "fetch_rag_enabled", lambda: True)
    monkeypatch.setattr(vaf.settings, "rag_chunk_size", 60)
    monkeypatch.setattr(vaf.settings, "rag_chunk_overlap", 15)
    monkeypatch.setattr(doc_embedding, "embed_texts", _fake_embed)


def _make_case(client):
    r = client.post("/api/cases", json={"borrower_name": "Acme", "segment": "corporate",
                                        "relationship": "etb", "industry_code": "IND-STEEL"},
                    headers=ANALYST1)
    assert r.status_code == 201, r.text
    return r.json()


def _upload(client, case_id, name, content):
    return client.post(f"/api/cases/{case_id}/documents",
                       files={"file": (name, io.BytesIO(content), "text/plain")},
                       headers=ANALYST1)


def test_intake_indexes_and_retrieve_ranks_relevant_passage(client, rag_on):
    case = _make_case(client)
    up = _upload(client, case["id"], "report.txt", DOC_BODY.encode())
    assert up.status_code == 201
    doc_id = up.json()["id"]

    r = client.post("/api/documents/retrieve",
                    json={"doc_ids": [doc_id], "query": "cash flow from operations", "top_k": 1},
                    headers=SERVICE)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["query_embedded"] is True
    hits = res["results"][0]["chunks"]
    assert hits and "cash flow" in hits[0]["text"].lower()


def test_intake_stays_ready_when_embedding_unavailable(client, monkeypatch):
    # RAG on, but the embedding egress is down -> intake must still succeed and
    # simply store no chunks (retrieval later falls back to full text).
    monkeypatch.setattr(vaf, "fetch_rag_enabled", lambda: True)
    monkeypatch.setattr(doc_embedding, "embed_texts", lambda texts: None)
    case = _make_case(client)
    up = _upload(client, case["id"], "r.txt", b"Some readable text with content.")
    assert up.status_code == 201
    assert up.json()["status"] == "ready"
    doc_id = up.json()["id"]
    r = client.post("/api/documents/retrieve",
                    json={"doc_ids": [doc_id], "query": "content", "top_k": 3},
                    headers=SERVICE).json()
    assert r["results"][0]["chunks"] == []  # nothing indexed


def test_no_embedding_when_rag_disabled(client, monkeypatch):
    monkeypatch.setattr(vaf, "fetch_rag_enabled", lambda: False)
    calls = {"n": 0}
    monkeypatch.setattr(doc_embedding, "embed_texts",
                        lambda texts: calls.__setitem__("n", calls["n"] + 1) or [[0.0]])
    case = _make_case(client)
    up = _upload(client, case["id"], "r.txt", b"Ready text here.")
    assert up.status_code == 201
    assert calls["n"] == 0  # embedding never called when RAG is off


def test_retrieve_missing_document_is_reported_not_error(client, rag_on):
    r = client.post("/api/documents/retrieve",
                    json={"doc_ids": ["does-not-exist"], "query": "x", "top_k": 3},
                    headers=SERVICE)
    assert r.status_code == 200
    assert r.json()["results"][0]["reason"] == "not_found"


def test_reindex_embeds_on_demand(client, monkeypatch):
    # Uploaded while RAG was off (no chunks); an admin later enables RAG and
    # reindexes the document explicitly.
    monkeypatch.setattr(vaf, "fetch_rag_enabled", lambda: False)
    monkeypatch.setattr(vaf.settings, "rag_chunk_size", 60)
    monkeypatch.setattr(vaf.settings, "rag_chunk_overlap", 15)
    monkeypatch.setattr(doc_embedding, "embed_texts", _fake_embed)
    case = _make_case(client)
    up = _upload(client, case["id"], "r.txt", DOC_BODY.encode())
    doc_id = up.json()["id"]

    pre = client.post("/api/documents/retrieve",
                      json={"doc_ids": [doc_id], "query": "cash flow", "top_k": 3},
                      headers=SERVICE).json()
    assert pre["results"][0]["chunks"] == []

    rx = client.post(f"/api/documents/{doc_id}/reindex", headers=ANALYST1)
    assert rx.status_code == 200 and rx.json()["chunks"] >= 1

    post = client.post("/api/documents/retrieve",
                       json={"doc_ids": [doc_id], "query": "cash flow from operations",
                             "top_k": 1}, headers=SERVICE).json()
    assert post["results"][0]["chunks"], post
