"""Azure AI Search backend — REST calls mocked with httpx.MockTransport:
index creation, chunk upsert, vector vs keyword query bodies, and fail-open."""
from __future__ import annotations

import json

import httpx
import pytest

from cam.services.document import azure_search


@pytest.fixture()
def azure_cfg(monkeypatch):
    s = azure_search.settings
    monkeypatch.setattr(s, "retrieval_backend", "azure_search")
    monkeypatch.setattr(s, "azure_search_endpoint", "https://res.search.windows.net")
    monkeypatch.setattr(s, "azure_search_api_version", "2024-07-01")
    monkeypatch.setattr(s, "azure_search_api_key_env", "CAM_AZ_SEARCH_KEY_TEST")
    monkeypatch.setattr(s, "azure_search_index", "cam-chunks")
    monkeypatch.setenv("CAM_AZ_SEARCH_KEY_TEST", "search-secret")
    return s


def _mock(monkeypatch, handler):
    real = httpx.Client  # capture before patching (avoid recursion)
    monkeypatch.setattr(azure_search.httpx, "Client",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))


def test_enabled_gated_on_backend_and_endpoint(azure_cfg, monkeypatch):
    assert azure_search.enabled() is True
    monkeypatch.setattr(azure_cfg, "retrieval_backend", "local")
    assert azure_search.enabled() is False


def test_ensure_index_creates_when_missing(azure_cfg, monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={})
        if request.method == "PUT":
            seen["key"] = request.headers.get("api-key")
            seen["schema"] = json.loads(request.content)
            return httpx.Response(201, json={})
        return httpx.Response(200, json={})

    _mock(monkeypatch, handler)
    assert azure_search.ensure_index(1536) is True
    assert seen["key"] == "search-secret"
    vec_field = [f for f in seen["schema"]["fields"] if f["name"] == "vector"][0]
    assert vec_field["dimensions"] == 1536
    assert seen["schema"]["vectorSearch"]["profiles"]


def test_upsert_builds_actions_with_vectors(azure_cfg, monkeypatch):
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={})  # index exists
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"value": [{"status": True}, {"status": True}]})

    _mock(monkeypatch, handler)
    n = azure_search.upsert_chunks("doc-1", "case-1", [
        {"ordinal": 0, "text": "alpha", "char_start": 0, "char_end": 5, "vector": [0.1, 0.2]},
        {"ordinal": 1, "text": "beta", "char_start": 5, "char_end": 9, "vector": [0.3, 0.4]}])
    assert n == 2
    assert "/indexes/cam-chunks/docs/index" in seen["url"]
    acts = seen["body"]["value"]
    assert acts[0]["id"] == "doc-1-0" and acts[0]["doc_id"] == "doc-1"
    assert acts[0]["@search.action"] == "mergeOrUpload" and acts[0]["vector"] == [0.1, 0.2]


def test_search_embedding_sends_vector_query(azure_cfg, monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"value": [
            {"ordinal": 142, "text": "cash flow", "char_start": 0, "char_end": 9,
             "@search.score": 0.88}]})

    _mock(monkeypatch, handler)
    hits = azure_search.search_one("doc-1", "cash flow", [0.1, 0.2, 0.3], top_k=3, mode="embedding")
    assert seen["body"]["vectorQueries"][0]["vector"] == [0.1, 0.2, 0.3]
    assert seen["body"]["filter"] == "doc_id eq 'doc-1'"
    assert hits == [{"ordinal": 142, "text": "cash flow", "score": 0.88,
                     "char_start": 0, "char_end": 9}]


def test_search_keyword_sends_bm25_no_vector(azure_cfg, monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"value": []})

    _mock(monkeypatch, handler)
    azure_search.search_one("doc-1", "cash flow", None, top_k=3, mode="keyword")
    assert seen["body"]["search"] == "cash flow"
    assert "vectorQueries" not in seen["body"]


def test_search_fail_open_returns_empty(azure_cfg, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("search down")

    _mock(monkeypatch, handler)
    assert azure_search.search_one("doc-1", "q", [0.1], top_k=3, mode="embedding") == []
    assert azure_search.upsert_chunks("doc-1", "c", [{"ordinal": 0, "text": "x"}]) == 0
