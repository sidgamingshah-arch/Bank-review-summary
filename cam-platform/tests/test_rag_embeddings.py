"""Embedding egress: deterministic mock embedder, the service-only /embed
route, the OpenAI-compatible embedder (key never leaks), and config/reload."""
from __future__ import annotations

import json
import math

import httpx
import pytest
from fastapi.testclient import TestClient

import cam.services.genai.main as genai
from cam.common.config import get_settings
from cam.common.errors import ApiError
from cam.services.genai import providers


@pytest.fixture(autouse=True)
def _reset_genai(monkeypatch):
    # no admin overrides; rebuild provider + embedder from env each test
    monkeypatch.setattr(genai, "_load_overrides", lambda: {})
    genai._overrides = None
    genai._provider = None
    genai._embedder = None
    yield
    genai._provider = None
    genai._embedder = None
    genai._overrides = None


# ---- mock embedder --------------------------------------------------------

def test_mock_embedder_is_deterministic_and_unit_norm():
    emb = providers.MockEmbedder(get_settings("tests"))
    r1 = emb.embed(["cash flow from operations was strong"])
    r2 = emb.embed(["cash flow from operations was strong"])
    assert r1.vectors == r2.vectors                       # stable across calls
    v = r1.vectors[0]
    assert len(v) == emb.dim >= 16
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, abs_tol=1e-6)
    # unrelated text embeds differently
    assert emb.embed(["total assets on the balance sheet"]).vectors[0] != v


# ---- /api/genai/embed route ----------------------------------------------

def test_embed_route_is_service_only(service_headers, analyst_headers):
    with TestClient(genai.app) as c:
        assert c.post("/api/genai/embed", json={"texts": ["x"]}).status_code in (401, 403)
        assert c.post("/api/genai/embed", json={"texts": ["x"]},
                      headers=analyst_headers).status_code == 403
        r = c.post("/api/genai/embed", json={"texts": ["one", "two"]},
                   headers=service_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["embeddings"]) == 2
    assert body["dim"] >= 16 and len(body["embeddings"][0]) == body["dim"]


def test_config_reports_embedding_egress(service_headers):
    with TestClient(genai.app) as c:
        cfg = c.get("/api/genai/config", headers=service_headers).json()
    assert cfg["embed_provider"] == "mock"
    assert {"embed_model", "embed_base_url", "embed_dim",
            "embed_api_key_env", "embed_api_key_configured"} <= set(cfg)


def test_reload_rebuilds_embedder(monkeypatch, service_headers):
    monkeypatch.setattr(genai, "_load_overrides", lambda: {})
    genai._embedder = object()  # stale
    with TestClient(genai.app) as c:
        assert c.post("/api/genai/reload", headers=service_headers).status_code == 200
    assert genai._embedder is None  # reset so it rebuilds from fresh config


# ---- OpenAI-compatible embedder ------------------------------------------

def _openai_embedder(monkeypatch, handler, *, key="sk-secret-xyz"):
    s = get_settings("tests")
    monkeypatch.setattr(s, "genai_embed_provider", "openai")
    monkeypatch.setattr(s, "genai_embed_base_url", "https://llm.example/v1")
    monkeypatch.setattr(s, "genai_embed_model", "text-embedding-3-small")
    monkeypatch.setattr(s, "genai_embed_api_key_env", "CAM_EMBED_KEY_TEST")
    monkeypatch.setenv("CAM_EMBED_KEY_TEST", key)
    emb = providers.OpenAIEmbedder(s)
    real = httpx.Client  # capture before swap; preserve the auth header it built
    emb.client = real(transport=httpx.MockTransport(handler), headers=emb.client.headers)
    return emb


def test_openai_embedder_posts_to_embeddings_and_sends_key(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [
            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            {"index": 1, "embedding": [0.4, 0.5, 0.6]}], "usage": {"prompt_tokens": 9}})

    emb = _openai_embedder(monkeypatch, handler)
    result = emb.embed(["alpha", "beta"])
    assert seen["url"].endswith("/embeddings")
    assert seen["body"] == {"model": "text-embedding-3-small", "input": ["alpha", "beta"]}
    assert seen["auth"] == "Bearer sk-secret-xyz"
    assert result.vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert result.dim == 3 and result.usage["input_tokens"] == 9


def test_openai_embedder_reorders_by_index(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"data": [
            {"index": 1, "embedding": [9.0]},
            {"index": 0, "embedding": [1.0]}]})

    emb = _openai_embedder(monkeypatch, handler)
    assert emb.embed(["first", "second"]).vectors == [[1.0], [9.0]]


def test_openai_embedder_http_error_maps_502_and_hides_key(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    emb = _openai_embedder(monkeypatch, handler, key="sk-topsecret")
    with pytest.raises(ApiError) as ei:
        emb.embed(["x"])
    assert ei.value.status == 502
    assert "sk-topsecret" not in str(ei.value.message)


def test_make_embedder_dispatch():
    assert isinstance(providers.make_embedder(get_settings("tests")), providers.MockEmbedder)
