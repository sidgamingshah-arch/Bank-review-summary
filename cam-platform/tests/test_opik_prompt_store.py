"""Opik prompt store: section prompts are the system-of-record in Opik.

Covers the offline default (local stand-in ref + snapshot read), the scope
(only section prompts, not standing/agent rules), and — against a simulated Opik
backend — publish write-through + resolve read-through, plus fail-open.

Uses per-test unique keys because the master-config DB persists across the suite.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

import cam.services.master_config.main as mc
from tests.test_master_config import publish


def _section(code):
    return {"section_code": code, "section_name": "Opik Section", "scope": "section",
            "prompt_text": "Assess the borrower {{borrower_name}}.",
            "source_doc_types": [], "uses_industry_kpis": False}


def _template(section_code):
    return {"name": "Opik T", "segment": "corporate", "relationship": "etb",
            "template_instructions": "House style.",
            "sections": [{"order": 1, "section_code": section_code, "mandatory": True,
                          "include_if_doctype": None, "length_guidance": "100 words",
                          "fixed_format": False}],
            "required_doc_types": []}


def _create_and_publish_prompt(c, key, admin, admin2, payload=None):
    assert c.post("/api/masters/prompts", json={"key": key, "payload": payload or _section(key)},
                  headers=admin).status_code == 201
    publish(c, "prompts", key, admin, admin2)


def _opik_ref(c, headers, key, n=1):
    """The Opik reference stamped on a published prompt version's provenance."""
    r = c.get(f"/api/masters/prompts/{key}/versions/{n}", headers=headers)
    assert r.status_code == 200, r.text
    return (r.json().get("provenance") or {}).get("opik")


class _FakePrompt:
    def __init__(self, name, commit, text):
        self.name, self.commit, self.version, self.prompt = name, commit, commit, text


class _FakeOpik:
    def __init__(self):
        self.store = {}

    def create_prompt(self, name, prompt, metadata=None, change_description=None, **k):
        import hashlib
        commit = "c-" + hashlib.sha256(prompt.encode()).hexdigest()[:8]
        self.store[(name, commit)] = prompt
        return _FakePrompt(name, commit, prompt)

    def get_prompt(self, name, commit=None, **k):
        text = self.store.get((name, commit))
        return _FakePrompt(name, commit, text) if text is not None else None


def test_section_prompt_publish_stamps_local_ref_offline(admin_headers, admin2_headers):
    with TestClient(mc.app) as c:
        _create_and_publish_prompt(c, "opik_s_local", admin_headers, admin2_headers)
        ref = _opik_ref(c, admin_headers, "opik_s_local")
        assert ref["backend"] == "local" and ref["commit"].startswith("local-")


def test_standing_rules_are_not_stored_in_opik(admin_headers, admin2_headers):
    with TestClient(mc.app) as c:
        if c.get("/api/masters/prompts/global_standing_rules", headers=admin_headers).status_code == 404:
            payload = {**_section("global_standing_rules"), "scope": "global",
                       "prompt_text": "Never fabricate numbers."}
            _create_and_publish_prompt(c, "global_standing_rules", admin_headers, admin2_headers, payload)
        assert _opik_ref(c, admin_headers, "global_standing_rules") is None


def test_opik_backend_write_and_read_through(admin_headers, admin2_headers, monkeypatch):
    fake = _FakeOpik()
    monkeypatch.setattr(mc.prompt_store, "enabled", lambda: True)
    monkeypatch.setattr(mc.prompt_store, "_opik", lambda: fake)
    with TestClient(mc.app) as c:
        _create_and_publish_prompt(c, "opik_s_be", admin_headers, admin2_headers)
        c.post("/api/masters/templates", json={"key": "opik_t_be", "payload": _template("opik_s_be")},
               headers=admin_headers)
        publish(c, "templates", "opik_t_be", admin_headers, admin2_headers)

        ref = _opik_ref(c, admin_headers, "opik_s_be")
        assert ref["backend"] == "opik" and ref["commit"]
        assert (ref["name"], ref["commit"]) in fake.store

        # prove the run reads content FROM Opik, not the DB snapshot: mutate Opik
        fake.store[(ref["name"], ref["commit"])] = "AUTHORITATIVE FROM OPIK"
        resolved = c.get("/api/masters/resolve/template/opik_t_be", headers=admin_headers).json()
        assert resolved["sections"][0]["prompt"]["payload"]["prompt_text"] == "AUTHORITATIVE FROM OPIK"


def test_publish_is_fail_open_when_opik_errors(admin_headers, admin2_headers, monkeypatch):
    class _Boom:
        def create_prompt(self, **k):
            raise RuntimeError("opik down")

    monkeypatch.setattr(mc.prompt_store, "enabled", lambda: True)
    monkeypatch.setattr(mc.prompt_store, "_opik", lambda: _Boom())
    with TestClient(mc.app) as c:
        _create_and_publish_prompt(c, "opik_s_boom", admin_headers, admin2_headers)  # must still succeed
        assert _opik_ref(c, admin_headers, "opik_s_boom")["backend"] == "local"


def test_status_endpoint(admin_headers):
    with TestClient(mc.app) as c:
        st = c.get("/api/masters/opik/status", headers=admin_headers).json()
        assert st["backend"] in ("local", "opik") and "enabled" in st
