"""Standalone rules service: the assemble/house endpoints, service-only access,
byte-parity with the in-process engine, and the genai fail-open client."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_service_headers, make_user_headers

from cam.common import rules_engine
from cam.services.genai import rules_client
from cam.services.rules import main as rules_main

SERVICE = make_service_headers("genai")

PAYLOAD = {
    "layers": {"global_rules": "GLOBAL-STANDING-RULE", "template_instructions": "TEMPLATE-INSTR"},
    "preferences": {"tonality": "crisp", "structure_bias": "bullets",
                    "table_usage": "auto", "length": "standard"},
    "fixed_format": False,
    "length_guidance": "keep to ~200 words",
    "agent_rules": "AGENT-SUMMARISATION-RULE",
}


@pytest.fixture()
def client():
    with TestClient(rules_main.app) as c:
        yield c


def test_assemble_composes_layers(client):
    r = client.post("/api/rules/assemble", json=PAYLOAD, headers=SERVICE)
    assert r.status_code == 200, r.text
    system = r.json()["system"]
    assert "NO FABRICATION" in system                       # house rules always present
    assert "GLOBAL-STANDING-RULE" in system                 # global standing rules layer
    assert "AGENT-SUMMARISATION-RULE" in system             # agent-role rules layer
    assert "TEMPLATE-INSTR" in system                       # template instructions layer
    assert "short sentences" in system                      # crisp tonality style directive


def test_assemble_byte_parity_with_engine(client):
    system = client.post("/api/rules/assemble", json=PAYLOAD, headers=SERVICE).json()["system"]
    local = rules_engine.build_system(PAYLOAD["layers"], PAYLOAD["preferences"],
                                      PAYLOAD["fixed_format"], PAYLOAD["length_guidance"],
                                      agent_rules=PAYLOAD["agent_rules"])
    assert system == local


def test_assemble_is_service_only(client):
    r = client.post("/api/rules/assemble", json=PAYLOAD,
                    headers=make_user_headers("analyst1", ["analyst"]))
    assert r.status_code == 403


def test_house_endpoint(client):
    r = client.get("/api/rules/house", headers=SERVICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "NO FABRICATION" in body["house_rules"]
    assert set(body["vocabulary"]) == {"tonality", "structure_bias", "table_usage", "length"}


# ---- genai fail-open client ------------------------------------------------

def test_compose_falls_back_when_disabled(monkeypatch):
    monkeypatch.setattr(rules_client.settings, "rules_service_enabled", False)
    # gateway_client must NOT be called when disabled
    monkeypatch.setattr(rules_client, "gateway_client",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    out = rules_client.compose_system({"global_rules": "G"}, None, False, None)
    assert out == rules_engine.build_system({"global_rules": "G"}, None, False, None)


def test_compose_falls_back_when_unreachable(monkeypatch):
    monkeypatch.setattr(rules_client.settings, "rules_service_enabled", True)

    def boom(*a, **k):
        raise httpx.ConnectError("gateway down")

    monkeypatch.setattr(rules_client, "gateway_client", boom)
    out = rules_client.compose_system({"global_rules": "G"}, {"tonality": "crisp"}, False, None)
    assert out == rules_engine.build_system({"global_rules": "G"}, {"tonality": "crisp"}, False, None)


def test_compose_uses_service_when_reachable(monkeypatch):
    monkeypatch.setattr(rules_client.settings, "rules_service_enabled", True)

    class FakeResp:
        status_code = 200

        def json(self):
            return {"system": "FROM-RULES-SERVICE"}

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(rules_client, "gateway_client", lambda *a, **k: FakeClient())
    monkeypatch.setattr(rules_client, "gateway_headers", lambda *a, **k: {})
    assert rules_client.compose_system({}, None, False, None) == "FROM-RULES-SERVICE"
