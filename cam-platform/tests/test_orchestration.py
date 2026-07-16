"""orchestration: run creation snapshots, gaps, queue worker, retry, regenerate."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import cam.services.orchestration.main as orch
from cam.services.orchestration import resolver, worker
from cam.services.orchestration.worker import build_gap_trailer, render_kpi_block
from tests.conftest import make_user_headers

FIN_TEXT = "Revenue FY2025 Rs. 4,210 Cr, up 12.5%. EBITDA margin 18.2%."

RESOLVED_TEMPLATE = {
    "template_key": "corp-etb", "template_version": 3,
    "template": {"name": "Corporate CAM", "segment": "corporate", "relationship": "etb",
                 "template_instructions": "UK English.",
                 "required_doc_types": ["audited_financials", "sanction_letter"]},
    "global_rules": {"prompt_key": "global_standing_rules", "version": 2,
                     "prompt_text": "Never speculate."},
    "sections": [
        {"order": 1, "section_code": "exec_summary", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "", "fixed_format": True,
         "prompt": {"key": "exec_summary", "version": 4, "payload": {
             "section_code": "exec_summary", "section_name": "Executive Summary",
             "prompt_text": "Summarise {{borrower_name}}.", "source_doc_types": ["audited_financials"],
             "uses_industry_kpis": False}}},
        {"order": 2, "section_code": "financial_analysis", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "300 words", "fixed_format": False,
         "prompt": {"key": "financial_analysis", "version": 7, "payload": {
             "section_code": "financial_analysis", "section_name": "Financial Analysis",
             "prompt_text": "Analyse {{borrower_name}} with {{industry_kpis}}.",
             "source_doc_types": ["audited_financials"], "uses_industry_kpis": True}}},
        {"order": 3, "section_code": "project_review", "mandatory": False,
         "include_if_doctype": "project_report", "length_guidance": "", "fixed_format": False,
         "prompt": {"key": "project_review", "version": 1, "payload": {
             "section_code": "project_review", "section_name": "Project Review",
             "prompt_text": "Review the project.", "source_doc_types": ["project_report"],
             "uses_industry_kpis": False}}},
    ],
    "doctype_master_versions": {"audited_financials": 2, "sanction_letter": 1, "project_report": 1},
    "settings": {"tagging_confidence_threshold": 0.55},
}

KPI_SET = {"industry": {"industry_code": "steel", "industry_name": "Steel"},
           "kpi_set_version": 5,
           "kpis": [{"code": "ebitda_t", "name": "EBITDA per tonne", "definition": "Op. profit/t",
                     "unit": "INR/t", "polarity": "higher_better", "benchmark": "4500",
                     "sections": ["financial_analysis"]},
                    {"code": "other", "name": "Debt/EBITDA", "definition": "", "unit": "x",
                     "polarity": "lower_better", "benchmark": None, "sections": ["risk"]}]}

CASE = {"id": "case-1", "borrower_name": "Acme Steel Ltd", "segment": "corporate",
        "relationship": "etb", "industry_code": "steel", "created_by": "analyst1",
        "status": "open"}

DOCS = [{"id": "doc-1", "filename": "fin2025.pdf", "status": "ready",
         "tags": [{"doctype_code": "audited_financials", "period_label": "FY2025",
                   "seq_order": 1}]},
        {"id": "doc-2", "filename": "sanction.pdf", "status": "ready",
         "tags": [{"doctype_code": "sanction_letter", "period_label": None, "seq_order": None}]},
        {"id": "doc-3", "filename": "malware.pdf", "status": "quarantined",
         "tags": [{"doctype_code": "project_report", "period_label": None, "seq_order": None}]}]

PREFS = {"tonality": "crisp", "structure_bias": "paragraphs", "table_usage": "auto",
         "length": "standard", "scope": "user"}


@pytest.fixture
def wired(monkeypatch):
    """Wire every cross-service call to in-memory fakes and capture outputs."""
    created_cams: list[dict] = []
    pushed_versions: list[tuple] = []
    genai_calls: list[dict] = []

    def fake_genai(payload):
        genai_calls.append(payload)
        return {"content": f"Draft for {payload['layers']['section_prompt'][:40]} — Rs. 4,210 Cr.",
                "model": "mock-cam-composer-v1",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "untraceable_numbers": []}

    def fake_create_cam(payload):
        cam = {"id": "cam-1", **payload,
               "sections": [{**s, "id": f"os-{s['section_code']}"} for s in payload["sections"]]}
        created_cams.append(cam)
        return cam

    monkeypatch.setattr(resolver, "fetch_resolved_template", lambda k: RESOLVED_TEMPLATE)
    monkeypatch.setattr(resolver, "fetch_kpi_set", lambda c: KPI_SET)
    monkeypatch.setattr(resolver, "fetch_case", lambda c: CASE)
    monkeypatch.setattr(resolver, "fetch_case_documents", lambda c: DOCS)
    monkeypatch.setattr(resolver, "fetch_document_text", lambda d: FIN_TEXT)
    monkeypatch.setattr(resolver, "fetch_user_preferences", lambda h: PREFS)
    monkeypatch.setattr(resolver, "genai_generate", fake_genai)
    monkeypatch.setattr(resolver, "create_cam", fake_create_cam)
    monkeypatch.setattr(resolver, "fetch_cam", lambda cid: created_cams[-1])
    monkeypatch.setattr(resolver, "push_section_version",
                        lambda cam_id, sid, content: pushed_versions.append((cam_id, sid, content)))
    return {"cams": created_cams, "pushed": pushed_versions, "genai": genai_calls}


def _create_run(c, headers, **kwargs):
    return c.post("/api/runs", json={"case_id": "case-1", "template_key": "corp-etb",
                                     "proceed_with_gaps": True, **kwargs}, headers=headers)


def test_run_end_to_end(wired, analyst_headers, captured_audit):
    with TestClient(orch.app) as c:
        r = _create_run(c, analyst_headers)
        assert r.status_code == 202, r.text
        run = r.json()

        # snapshot of every master version used (FR-A07/FR-F01)
        assert run["master_versions"] == {
            "template": 3, "prompts": {"exec_summary": 4, "financial_analysis": 7,
                                       "project_review": 1},
            "global_rules": 2,
            "doctypes": {"audited_financials": 2, "sanction_letter": 1, "project_report": 1},
            "kpi_set": 5}
        assert run["applied_preferences"]["source"] == "user"
        assert run["gaps"] == []  # both required types present
        by_code = {s["section_code"]: s for s in run["sections"]}
        # conditional section skipped: its only doc is quarantined (FR-C02 + include-if)
        assert by_code["project_review"]["status"] == "skipped"
        assert by_code["exec_summary"]["status"] == "queued"

        assert worker.drain() == 2  # two queued sections processed
        run = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
        assert run["status"] == "complete"
        assert run["model_identity"] == "mock-cam-composer-v1"
        assert run["cam_id"] == "cam-1"

        # CAM handoff: completed sections + the _gaps trailer (FR-D05)
        cam = wired["cams"][0]
        codes = [s["section_code"] for s in cam["sections"]]
        assert codes == ["exec_summary", "financial_analysis", "_gaps"]
        assert cam["created_by"] == "analyst1"
        trailer = cam["sections"][-1]["content"]
        assert "Sections skipped" in trailer and "project_review" not in trailer  # skip listed by name
        assert "Project Review" in trailer

        # genai payload discipline: grounding only from mapped docs (FR-D03),
        # fixed-format section carries no preferences (FR-B04), KPI injection (FR-A11)
        exec_call = wired["genai"][0]
        assert exec_call["fixed_format"] is True and exec_call["preferences"] is None
        fin_call = wired["genai"][1]
        assert fin_call["preferences"]["tonality"] == "crisp"
        assert "EBITDA per tonne" in fin_call["placeholders"]["industry_kpis"]
        assert "Debt/EBITDA" not in fin_call["placeholders"]["industry_kpis"]  # section-scoped
        assert [d["doctype_code"] for d in fin_call["grounding_docs"]] == ["audited_financials"]
        assert "Acme Steel Ltd" in fin_call["layers"]["section_prompt"]

        # audit trail carries the run record (FR-F01)
        actions = [e["action"] for e in captured_audit]
        assert "run.started" in actions and "run.completed" in actions
        completed = next(e for e in captured_audit if e["action"] == "run.completed")
        assert completed["detail"]["master_versions"]["template"] == 3


def test_gaps_conflict_and_proceed(wired, analyst_headers, monkeypatch):
    docs_no_sanction = [d for d in DOCS if d["id"] != "doc-2"]
    monkeypatch.setattr(resolver, "fetch_case_documents", lambda c: docs_no_sanction)
    with TestClient(orch.app) as c:
        r = c.post("/api/runs", json={"case_id": "case-1", "template_key": "corp-etb"},
                   headers=analyst_headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"

        r = _create_run(c, analyst_headers)
        assert r.status_code == 202
        run = r.json()
        assert run["gaps"][0]["doctype_code"] == "sanction_letter"
        worker.drain()
        trailer = wired["cams"][-1]["sections"][-1]["content"]
        assert "sanction_letter" in trailer and "Missing required documents" in trailer


def test_retry_failed_section(wired, analyst_headers, monkeypatch):
    calls = {"n": 0}

    def flaky_genai(payload):
        calls["n"] += 1
        if payload["layers"]["section_prompt"].startswith("Analyse") and calls["n"] <= 2:
            raise RuntimeError("model endpoint unavailable")
        return {"content": "ok content", "model": "mock", "usage": {}, "untraceable_numbers": []}

    monkeypatch.setattr(resolver, "genai_generate", flaky_genai)
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
        assert state["status"] == "partial"
        fin = next(s for s in state["sections"] if s["section_code"] == "financial_analysis")
        assert fin["status"] == "failed" and "unavailable" in fin["error"]

        r = c.post(f"/api/runs/{run['id']}/sections/financial_analysis/retry",
                   headers=analyst_headers)
        assert r.status_code == 202
        worker.drain()
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
        fin = next(s for s in state["sections"] if s["section_code"] == "financial_analysis")
        assert fin["status"] == "complete" and fin["attempts"] == 2


def test_regenerate_pushes_new_version(wired, analyst_headers):
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        r = c.post(f"/api/runs/{run['id']}/sections/financial_analysis/regenerate",
                   headers=analyst_headers)
        assert r.status_code == 202
        worker.drain()
        assert wired["pushed"], "regeneration must push a new section version"
        cam_id, section_id, content = wired["pushed"][0]
        assert cam_id == "cam-1" and section_id == "os-financial_analysis" and content


def test_rate_limit_and_scoping(wired, analyst_headers, reviewer_headers):
    with TestClient(orch.app) as c:
        first = _create_run(c, analyst_headers).json()
        _create_run(c, analyst_headers)
        r = _create_run(c, analyst_headers)  # third active run for same user
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"

        # another analyst cannot read this run; a reviewer can
        r = c.get(f"/api/runs/{first['id']}", headers=make_user_headers("analyst2", ["analyst"]))
        assert r.status_code == 403
        assert c.get(f"/api/runs/{first['id']}", headers=reviewer_headers).status_code == 200
        # auditor cannot launch generation (BRD §4)
        r = _create_run(c, make_user_headers("auditor1", ["auditor"]))
        assert r.status_code == 403
        worker.drain()


def test_usage_summary_roles(wired, analyst_headers, auditor_headers):
    with TestClient(orch.app) as c:
        r = c.get("/api/runs/usage/summary", headers=auditor_headers)
        assert r.status_code == 200 and "tokens_out" in r.json()
        assert c.get("/api/runs/usage/summary", headers=analyst_headers).status_code == 403


def test_kpi_block_and_trailer_helpers():
    block = render_kpi_block(KPI_SET["kpis"], "financial_analysis")
    assert "EBITDA per tonne (INR/t, higher is better; benchmark 4500)" in block
    assert render_kpi_block([], "x").startswith("(no industry KPIs")
