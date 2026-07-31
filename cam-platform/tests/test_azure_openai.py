"""Azure OpenAI chat provider + embedder: deployment-scoped URLs, api-key
header, reasoning-model params, and NFR-06 (key never leaks). HTTP is mocked."""
from __future__ import annotations

import json

import httpx
import pytest

from cam.common.config import get_settings
from cam.common.errors import ApiError
from cam.services.genai import providers


def _azure_settings(monkeypatch, *, reasoning=False, key="az-secret-123"):
    s = get_settings("tests")
    monkeypatch.setattr(s, "llm_provider", "azure")
    monkeypatch.setattr(s, "genai_embed_provider", "azure")
    monkeypatch.setattr(s, "azure_openai_endpoint", "https://res.openai.azure.com")
    monkeypatch.setattr(s, "azure_openai_api_version", "2024-10-21")
    monkeypatch.setattr(s, "azure_openai_api_key_env", "CAM_AZURE_KEY_TEST")
    monkeypatch.setattr(s, "azure_openai_reasoning", reasoning)
    monkeypatch.setattr(s, "genai_model", "gpt-4o-chat")
    monkeypatch.setattr(s, "genai_embed_model", "text-embedding-3-large")
    monkeypatch.setenv("CAM_AZURE_KEY_TEST", key)
    return s


def _with_transport(obj, handler):
    real = httpx.Client
    obj.client = real(transport=httpx.MockTransport(handler), headers=obj.client.headers)
    return obj


def test_azure_chat_url_header_and_body(monkeypatch):
    s = _azure_settings(monkeypatch)
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("api-key")
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4}})

    prov = _with_transport(providers.AzureOpenAIProvider(s), handler)
    result = prov.generate({}, "system", "user")
    assert "/openai/deployments/gpt-4o-chat/chat/completions" in seen["url"]
    assert "api-version=2024-10-21" in seen["url"]
    assert seen["key"] == "az-secret-123"        # api-key header, not Bearer
    assert seen["auth"] is None
    assert "model" not in seen["body"]            # deployment is in the URL
    assert seen["body"]["max_tokens"] >= 1 and "temperature" in seen["body"]
    assert result.content == "hello"
    assert result.usage == {"input_tokens": 11, "output_tokens": 4}
    assert result.model == "gpt-4o-chat"


def test_azure_reasoning_uses_max_completion_tokens_no_temperature(monkeypatch):
    s = _azure_settings(monkeypatch, reasoning=True)
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    prov = _with_transport(providers.AzureOpenAIProvider(s), handler)
    prov.generate({}, "system", "user")
    assert "max_completion_tokens" in seen["body"]
    assert "max_tokens" not in seen["body"]
    assert "temperature" not in seen["body"]      # reasoning models reject sampling


def test_azure_embedder_posts_to_deployment(monkeypatch):
    s = _azure_settings(monkeypatch)
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}],
                                         "usage": {"prompt_tokens": 3}})

    emb = _with_transport(providers.AzureOpenAIEmbedder(s), handler)
    result = emb.embed(["hello"])
    assert "/openai/deployments/text-embedding-3-large/embeddings" in seen["url"]
    assert "api-version=2024-10-21" in seen["url"]
    assert seen["key"] == "az-secret-123"
    assert result.vectors == [[0.1, 0.2]] and result.dim == 2


def test_azure_http_error_maps_502_and_hides_key(monkeypatch):
    s = _azure_settings(monkeypatch, key="az-topsecret")

    def handler(request):
        raise httpx.ConnectError("down")

    prov = _with_transport(providers.AzureOpenAIProvider(s), handler)
    with pytest.raises(ApiError) as ei:
        prov.generate({}, "system", "user")
    assert ei.value.status == 502
    assert "az-topsecret" not in str(ei.value.message)


def test_make_provider_and_embedder_dispatch_azure(monkeypatch):
    s = _azure_settings(monkeypatch)
    assert isinstance(providers.make_provider(s), providers.AzureOpenAIProvider)
    assert isinstance(providers.make_embedder(s), providers.AzureOpenAIEmbedder)


def test_azure_provider_requires_endpoint(monkeypatch):
    s = get_settings("tests")
    monkeypatch.setattr(s, "llm_provider", "azure")
    monkeypatch.setattr(s, "azure_openai_endpoint", "")
    monkeypatch.setattr(s, "genai_model", "gpt-4o-chat")
    with pytest.raises(ApiError):
        providers.AzureOpenAIProvider(s)
