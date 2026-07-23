"""The openai-compatible live provider: request shaping, response parsing,
error mapping, and NFR-06 (the API key is used but never leaked)."""
from __future__ import annotations

import json

import httpx
import pytest

from cam.common.config import Settings
from cam.common.errors import ApiError
from cam.services.genai import providers


def _settings(**kw):
    base = dict(service_name="genai", llm_provider="openai",
                genai_base_url="https://llm.example/v1", genai_model="m1",
                genai_api_key_env="TEST_LLM_KEY")
    base.update(kw)
    return Settings(**base)


def _provider_with(monkeypatch, handler, key="secret-abc"):
    if key is None:
        monkeypatch.delenv("TEST_LLM_KEY", raising=False)
    else:
        monkeypatch.setenv("TEST_LLM_KEY", key)
    prov = providers.OpenAICompatibleProvider(_settings())
    # swap in a MockTransport client, preserving the auth headers the provider built
    prov.client = httpx.Client(transport=httpx.MockTransport(handler), headers=prov.client.headers)
    return prov


def _ok(content, usage=True, finish="stop", model="m1-served"):
    body = {"model": model,
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 3}
    return httpx.Response(200, json=body)


def test_make_provider_selects_openai(monkeypatch):
    assert isinstance(providers.make_provider(_settings()), providers.OpenAICompatibleProvider)


def test_missing_base_url_is_misconfig():
    with pytest.raises(ApiError) as ei:
        providers.OpenAICompatibleProvider(_settings(genai_base_url=""))
    assert ei.value.code == "genai_misconfigured"


def test_request_shape_and_auth_and_parsing(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok("Assessment references 42 crore.")

    prov = _provider_with(monkeypatch, handler)
    assert prov.client.headers.get("authorization") == "Bearer secret-abc"
    result = prov.generate({}, "SYSTEM", "USER mentions 42")

    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer secret-abc"
    assert seen["body"]["model"] == "m1"
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]
    assert result.content == "Assessment references 42 crore."
    assert result.model == "m1-served"
    assert result.usage == {"input_tokens": 11, "output_tokens": 3}


def test_model_and_tokens_overridable(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok("ok")

    prov = _provider_with(monkeypatch, handler)
    prov.generate({"model_overrides": {"model": "m2", "max_tokens": 256, "temperature": 0.3}},
                  "s", "u")
    assert seen["body"]["model"] == "m2"
    assert seen["body"]["max_tokens"] == 256
    assert seen["body"]["temperature"] == 0.3


def test_usage_falls_back_when_absent(monkeypatch):
    prov = _provider_with(monkeypatch, lambda r: _ok("body text", usage=False))
    result = prov.generate({}, "system", "user")
    assert result.usage["input_tokens"] > 0 and result.usage["output_tokens"] > 0


def test_json_roles_pass_content_through(monkeypatch):
    facts = json.dumps({"facts": [{"item": "x", "value": "1", "unit": "", "source": "S", "quote": "q1"}]})
    prov = _provider_with(monkeypatch, lambda r: _ok(facts))
    assert json.loads(prov.extract({}, "s", "u").content)["facts"][0]["value"] == "1"

    verdict = json.dumps({"passed": True, "omissions": [], "flags": [], "notes": "ok"})
    prov = _provider_with(monkeypatch, lambda r: _ok(verdict))
    assert json.loads(prov.materiality({}, "s", "u").content)["passed"] is True


def test_edit_sets_rationale(monkeypatch):
    prov = _provider_with(monkeypatch, lambda r: _ok("edited"))
    result = prov.edit({}, "s", "u")
    assert result.content == "edited" and result.rationale


def test_http_error_maps_to_502_and_hides_key(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="upstream boom secret-abc leaked?")

    prov = _provider_with(monkeypatch, handler)
    with pytest.raises(ApiError) as ei:
        prov.generate({}, "s", "u")
    assert ei.value.status == 502 and ei.value.code == "genai_upstream_error"
    # NFR-06: neither the key nor the upstream body is echoed in the error
    assert "secret-abc" not in str(ei.value.message)


def test_connection_error_is_failsafe(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("unreachable")

    prov = _provider_with(monkeypatch, handler)
    with pytest.raises(ApiError) as ei:
        prov.generate({}, "s", "u")
    assert ei.value.code == "genai_upstream_error"


def test_content_filter_is_refusal(monkeypatch):
    prov = _provider_with(monkeypatch, lambda r: _ok("", finish="content_filter"))
    with pytest.raises(ApiError) as ei:
        prov.generate({}, "s", "u")
    assert ei.value.code == "model_refusal"


def test_no_key_means_no_auth_header(monkeypatch):
    prov = _provider_with(monkeypatch, lambda r: _ok("ok"), key=None)
    assert "authorization" not in {k.lower() for k in prov.client.headers.keys()}
