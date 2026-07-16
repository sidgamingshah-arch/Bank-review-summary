"""master-config: lifecycle, maker-checker, validation, resolution, CSV bulk."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

import cam.services.master_config.main as mc
from tests.conftest import make_user_headers

DOCTYPE = {"code": "audited_financials", "name": "Audited financials",
           "description": "Audited annual accounts", "synonyms": ["annual report"],
           "keywords": ["balance sheet", "profit and loss"], "active": True}
PROMPT = {"section_code": "financial_analysis", "section_name": "Financial Analysis",
          "scope": "section",
          "prompt_text": "Analyse the financials of {{borrower_name}} using {{doc:audited_financials}}.",
          "source_doc_types": ["audited_financials"], "uses_industry_kpis": True}
INDUSTRY = {"sector_code": "mfg", "sector_name": "Manufacturing",
            "industry_code": "steel", "industry_name": "Steel"}


def publish(client, mtype, key, headers_maker, headers_checker, version_no=1):
    r = client.post(f"/api/masters/{mtype}/{key}/versions/{version_no}/submit", headers=headers_maker)
    assert r.status_code == 200, r.text
    r = client.post(f"/api/masters/{mtype}/{key}/versions/{version_no}/approve", headers=headers_checker)
    assert r.status_code == 200, r.text
    return r.json()


def test_lifecycle_maker_checker(admin_headers, admin2_headers, analyst_headers):
    with TestClient(mc.app) as c:
        r = c.post("/api/masters/doctypes", json={"key": "audited_financials", "payload": DOCTYPE,
                                                  "change_note": "initial"}, headers=admin_headers)
        assert r.status_code == 201, r.text

        # analyst cannot draft
        r = c.post("/api/masters/doctypes", json={"key": "x1", "payload": DOCTYPE}, headers=analyst_headers)
        assert r.status_code == 403

        # approve before submit -> conflict
        r = c.post("/api/masters/doctypes/audited_financials/versions/1/approve", headers=admin2_headers)
        assert r.status_code == 409

        # maker cannot approve own submission (FR-A03)
        c.post("/api/masters/doctypes/audited_financials/versions/1/submit", headers=admin_headers)
        r = c.post("/api/masters/doctypes/audited_financials/versions/1/approve", headers=admin_headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "maker_checker_violation"

        r = c.post("/api/masters/doctypes/audited_financials/versions/1/approve", headers=admin2_headers)
        assert r.status_code == 200 and r.json()["status"] == "published"

        # v2 publish retires v1
        payload2 = {**DOCTYPE, "description": "updated"}
        r = c.post("/api/masters/doctypes/audited_financials/versions",
                   json={"payload": payload2, "change_note": "v2"}, headers=admin_headers)
        assert r.status_code == 201 and r.json()["version_no"] == 2
        publish(c, "doctypes", "audited_financials", admin_headers, admin2_headers, 2)
        item = c.get("/api/masters/doctypes/audited_financials", headers=admin_headers).json()
        assert item["published_version"] == 2
        assert [v["status"] for v in item["versions"]] == ["retired", "published"]

        # diff + rollback clone
        r = c.get("/api/masters/doctypes/audited_financials/diff?from=1&to=2", headers=admin_headers)
        assert "updated" in r.json()["diff"] and "+" in r.json()["diff"]
        r = c.post("/api/masters/doctypes/audited_financials/versions/1/rollback", headers=admin_headers)
        assert r.status_code == 201 and r.json()["version_no"] == 3
        assert r.json()["payload"]["description"] == DOCTYPE["description"]

        # analyst sees published version but not drafts
        r = c.get("/api/masters/doctypes/audited_financials/versions/2", headers=analyst_headers)
        assert r.status_code == 200
        r = c.get("/api/masters/doctypes/audited_financials/versions/3", headers=analyst_headers)
        assert r.status_code == 403

        # list summary carries SCALAR version fields (contract: the SPA renders them)
        listing = c.get("/api/masters/doctypes", headers=analyst_headers).json()
        entry = next(i for i in listing if i["key"] == "audited_financials")
        assert entry["latest_version"] == 3 and entry["published_version"] == 2
        assert entry["latest_status"] == "draft"
        assert isinstance(entry["latest_version"], int)


def test_prompt_placeholder_validation(admin_headers):
    with TestClient(mc.app) as c:
        bad = {**PROMPT, "section_code": "s1",
               "prompt_text": "Use {{unknown_thing}} and {{doc:missing_type}}."}
        r = c.post("/api/masters/prompts", json={"key": "s1", "payload": bad}, headers=admin_headers)
        assert r.status_code == 422
        details = " ".join(r.json()["error"]["details"])
        assert "unknown_thing" in details and "missing_type" in details


def test_template_referential_validation_and_resolution(admin_headers, admin2_headers):
    with TestClient(mc.app) as c:
        # doctype + prompt + global rules published
        if c.get("/api/masters/doctypes/audited_financials", headers=admin_headers).status_code == 404:
            c.post("/api/masters/doctypes", json={"key": "audited_financials", "payload": DOCTYPE},
                   headers=admin_headers)
            publish(c, "doctypes", "audited_financials", admin_headers, admin2_headers)
        r = c.post("/api/masters/prompts", json={"key": "financial_analysis", "payload": PROMPT},
                   headers=admin_headers)
        assert r.status_code == 201, r.text
        publish(c, "prompts", "financial_analysis", admin_headers, admin2_headers)
        c.post("/api/masters/prompts", json={
            "key": "global_standing_rules",
            "payload": {**PROMPT, "section_code": "global_standing_rules",
                        "section_name": "Global standing rules", "scope": "global",
                        "prompt_text": "Never fabricate numbers; flag anything untraceable.",
                        "source_doc_types": [], "uses_industry_kpis": False}}, headers=admin_headers)
        publish(c, "prompts", "global_standing_rules", admin_headers, admin2_headers)

        template = {"name": "Corporate CAM", "segment": "corporate", "relationship": "etb",
                    "template_instructions": "House style: professional credit language.",
                    "sections": [{"order": 1, "section_code": "financial_analysis",
                                  "mandatory": True, "include_if_doctype": None,
                                  "length_guidance": "300 words", "fixed_format": False}],
                    "required_doc_types": ["audited_financials"]}

        # unknown section binding rejected (FR-A14)
        bad = {**template, "sections": [{**template["sections"][0], "section_code": "nope"}]}
        r = c.post("/api/masters/templates", json={"key": "corp-bad", "payload": bad}, headers=admin_headers)
        assert r.status_code == 422

        r = c.post("/api/masters/templates", json={"key": "corp-etb", "payload": template},
                   headers=admin_headers)
        assert r.status_code == 201, r.text

        # resolution refuses unpublished template
        r = c.get("/api/masters/resolve/template/corp-etb", headers=admin_headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "not_published"

        publish(c, "templates", "corp-etb", admin_headers, admin2_headers)
        resolved = c.get("/api/masters/resolve/template/corp-etb", headers=admin_headers).json()
        assert resolved["template_version"] == 1
        assert resolved["sections"][0]["prompt"]["payload"]["section_code"] == "financial_analysis"
        assert resolved["global_rules"]["prompt_text"].startswith("Never fabricate")
        assert resolved["doctype_master_versions"]["audited_financials"] >= 1
        assert "tagging_confidence_threshold" in resolved["settings"]

        # published doctypes helper
        pub = c.get("/api/masters/published/doctypes", headers=admin_headers).json()
        assert any(d["code"] == "audited_financials" for d in pub)


def test_kpi_csv_bulk_and_export(admin_headers, admin2_headers):
    with TestClient(mc.app) as c:
        c.post("/api/masters/industries", json={"key": "steel", "payload": INDUSTRY},
               headers=admin_headers)
        publish(c, "industries", "steel", admin_headers, admin2_headers)

        csv_body = ("industry_code,kpi_code,kpi_name,definition,unit,polarity,benchmark,sections\n"
                    "steel,ebitda_per_tonne,EBITDA per tonne,Operating profit per tonne,INR/t,"
                    "higher_better,4500,financial_analysis|industry_analysis\n"
                    "steel,debt_ebitda,Debt / EBITDA,Leverage,x,lower_better,3.5x,financial_analysis\n"
                    ",missing,Bad row,,,wrong_polarity,,\n")
        r = c.post("/api/masters/kpi-sets/bulk",
                   files={"file": ("kpis.csv", io.BytesIO(csv_body.encode()), "text/csv")},
                   headers=admin_headers)
        body = r.json()
        assert r.status_code == 200, r.text
        assert [x["industry_code"] for x in body["created"]] == ["steel"]
        assert len(body["errors"]) == 1 and body["errors"][0]["row"] == 4

        v = body["created"][0]["version_no"]
        publish(c, "kpi-sets", "steel", admin_headers, admin2_headers, v)
        resolved = c.get("/api/masters/resolve/kpi-set/steel", headers=admin_headers).json()
        assert len(resolved["kpis"]) == 2 and resolved["industry"]["industry_name"] == "Steel"

        exported = c.get("/api/masters/kpi-sets/export.csv", headers=admin_headers)
        assert "ebitda_per_tonne" in exported.text

        # maker-checker applies to bulk drafts too: analyst cannot upload
        r = c.post("/api/masters/kpi-sets/bulk",
                   files={"file": ("kpis.csv", io.BytesIO(csv_body.encode()), "text/csv")},
                   headers=make_user_headers("analyst1", ["analyst"]))
        assert r.status_code == 403


def test_sandbox_test_uses_draft(admin_headers, monkeypatch):
    with TestClient(mc.app) as c:
        captured = {}

        def fake_genai(payload):
            captured.update(payload)
            return {"content": "SANDBOX DRAFT", "model": "mock", "usage": {"input_tokens": 1}}

        monkeypatch.setattr(mc, "call_genai_generate", fake_genai)
        key = "sandbox_section"
        c.post("/api/masters/prompts", json={"key": key, "payload": {
            **PROMPT, "section_code": key, "source_doc_types": [],
            "prompt_text": "Summarise {{borrower_name}} performance."}}, headers=admin_headers)
        r = c.post(f"/api/masters/prompts/{key}/sandbox-test",
                   json={"sample_docs": [{"doctype_code": "sample", "text": "Revenue Rs. 100 Cr"}],
                         "placeholders": {"borrower_name": "ACME Ltd"}},
                   headers=admin_headers)
        assert r.status_code == 200 and r.json()["content"] == "SANDBOX DRAFT"
        assert r.json()["version_tested"] == 1
        assert "ACME Ltd" in captured["layers"]["section_prompt"]
        assert captured["grounding_docs"][0]["text"] == "Revenue Rs. 100 Cr"


def test_settings_roundtrip(admin_headers, analyst_headers):
    with TestClient(mc.app) as c:
        r = c.get("/api/masters/settings", headers=analyst_headers)
        assert r.json()["tagging_confidence_threshold"] == 0.55
        r = c.put("/api/masters/settings", json={"tagging_confidence_threshold": 0.7},
                  headers=admin_headers)
        assert r.json()["tagging_confidence_threshold"] == 0.7
        r = c.put("/api/masters/settings", json={"tagging_confidence_threshold": 1.5},
                  headers=admin_headers)
        assert r.status_code == 422
        r = c.put("/api/masters/settings", json={"tagging_confidence_threshold": 0.5},
                  headers=analyst_headers)
        assert r.status_code == 403
