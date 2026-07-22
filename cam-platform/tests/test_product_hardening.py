"""Productization batch: late-section CAM join, stuck-job recovery, case
lifecycle, final-CAM regeneration guard, duplicate-grounding exclusion,
masters export/import bundle."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import cam.services.document.main as doc_main
import cam.services.master_config.main as mc
import cam.services.orchestration.main as orch
import cam.services.output.main as out_main
from cam.common.db import utcnow
from cam.services.orchestration import resolver, worker
from cam.services.orchestration.models import SectionJob
from tests.conftest import make_service_headers, make_user_headers

# reuse the orchestration test doubles (importing `wired` registers the fixture)
from tests.test_orchestration import CASE, DOCS, _create_run, wired  # noqa: F401

SERVICE = make_service_headers("orchestration")
ANALYST = make_user_headers("analyst1", ["analyst"])


# ------------------------------------------------------------- late-section join

def test_retried_section_joins_existing_cam(wired, analyst_headers, monkeypatch):
    """A section that fails during the run (CAM created without it) must join
    the CAM when its retry completes — as a NEW section, not a lost draft."""
    calls = {"n": 0}

    def flaky_genai(payload):
        calls["n"] += 1
        if payload["layers"]["section_prompt"].startswith("Analyse") and calls["n"] <= 2:
            raise RuntimeError("model endpoint unavailable")
        return {"content": "recovered content Rs. 4,210 Cr", "model": "mock",
                "usage": {}, "untraceable_numbers": []}

    added = []
    monkeypatch.setattr(resolver, "genai_generate", flaky_genai)
    monkeypatch.setattr(resolver, "create_cam_section",
                        lambda cam_id, payload: added.append((cam_id, payload)) or
                        {"section_id": "new", "version_no": 1, "created": True})
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
        assert state["status"] == "partial" and state["cam_id"] == "cam-1"
        cam_codes = [s["section_code"] for s in wired["cams"][0]["sections"]]
        assert "financial_analysis" not in cam_codes  # failed section absent

        c.post(f"/api/runs/{run['id']}/sections/financial_analysis/retry",
               headers=analyst_headers)
        worker.drain()

    assert added, "late-completed section must be added to the CAM"
    cam_id, payload = added[0]
    assert cam_id == "cam-1"
    assert payload["section_code"] == "financial_analysis" and payload["order"] == 2
    assert "recovered content" in payload["content"]


# ------------------------------------------------------------- stuck-job recovery

def test_reaper_requeues_lost_jobs_and_caps_attempts(wired, analyst_headers, monkeypatch):
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()

        job_id = worker._claim_next()
        assert job_id is not None
        stale = utcnow() - timedelta(seconds=worker.JOB_LEASE_SECONDS + 5)

        # worker died mid-job -> lease expires -> requeued (attempts below cap)
        with orch.SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.claimed_at = stale
            db.commit()
        assert worker.reap_stuck_jobs() == 1
        with orch.SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            assert job.status == "queued" and job.claimed_at is None

        # repeated losses exhaust the cap -> failed loudly, run settles
        with orch.SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.status = "running"
            job.attempts = worker.MAX_SECTION_ATTEMPTS
            job.claimed_at = stale
            db.commit()
        assert worker.reap_stuck_jobs() == 1
        with orch.SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            assert job.status == "failed" and "worker lost" in job.error

        worker.drain()  # finish the rest; run must settle despite the dead section
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
        assert state["status"] == "partial"

        # analyst can still retry the reaped section manually
        code = next(s["section_code"] for s in state["sections"] if s["status"] == "failed")
        r = c.post(f"/api/runs/{run['id']}/sections/{code}/retry", headers=analyst_headers)
        assert r.status_code == 202
        worker.drain()


# --------------------------------------------------------- final-CAM regen guard

def test_regenerate_refused_on_finalised_cam(wired, analyst_headers, monkeypatch):
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        monkeypatch.setattr(resolver, "fetch_cam",
                            lambda cam_id: {"id": cam_id, "status": "final", "sections": []})
        r = c.post(f"/api/runs/{run['id']}/sections/financial_analysis/regenerate",
                   headers=analyst_headers)
        assert r.status_code == 409
        assert "finalised" in r.json()["error"]["message"]


# ------------------------------------------------------- duplicate grounding

def test_duplicate_content_grounds_once(wired, analyst_headers, monkeypatch):
    dup_docs = [
        {"id": "doc-1", "filename": "fin2025.pdf", "status": "ready", "sha256": "aaa",
         "tags": [{"doctype_code": "audited_financials", "period_label": "FY2025",
                   "seq_order": 1}]},
        {"id": "doc-9", "filename": "fin2025_copy.pdf", "status": "ready", "sha256": "aaa",
         "tags": [{"doctype_code": "audited_financials", "period_label": "FY2025",
                   "seq_order": 2}]},
        {"id": "doc-2", "filename": "sanction.pdf", "status": "ready", "sha256": "bbb",
         "tags": [{"doctype_code": "sanction_letter"}]},
    ]
    monkeypatch.setattr(resolver, "fetch_case_documents", lambda c: dup_docs)
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        fin = next(s for s in run["sections"] if s["section_code"] == "financial_analysis")
        assert [d["doc_id"] for d in fin["input_documents"]] == ["doc-1"]
        worker.drain()


# ------------------------------------------------------------ case lifecycle

def test_case_status_notifications(wired, analyst_headers, monkeypatch):
    statuses = []
    monkeypatch.setattr(resolver, "update_case_status",
                        lambda case_id, status: statuses.append((case_id, status)))
    with TestClient(orch.app) as c:
        _create_run(c, analyst_headers)
        assert statuses[-1] == ("case-1", "generating")
        worker.drain()
        assert statuses[-1] == ("case-1", "drafted")


def test_case_status_endpoint_service_only():
    with TestClient(doc_main.app) as c:
        case = c.post("/api/cases", headers=ANALYST,
                      json={"borrower_name": "Lifecycle Co", "segment": "corporate",
                            "relationship": "etb", "industry_code": "steel"}).json()
        r = c.patch(f"/api/cases/{case['id']}/status", json={"status": "generating"},
                    headers=make_service_headers("orchestration"))
        assert r.status_code == 200 and r.json()["status"] == "generating"
        # end users cannot drive the lifecycle
        r = c.patch(f"/api/cases/{case['id']}/status", json={"status": "finalised"},
                    headers=ANALYST)
        assert r.status_code == 403
        # unknown states rejected
        r = c.patch(f"/api/cases/{case['id']}/status", json={"status": "bogus"},
                    headers=make_service_headers("orchestration"))
        assert r.status_code == 422


# --------------------------------------------------------- output: late add

def test_output_add_section_and_final_lock(admin_headers):
    service = make_service_headers("orchestration")
    with TestClient(out_main.app) as c:
        cam = c.post("/api/cams", headers=service, json={
            "case_id": "case-x", "run_id": "run-x", "title": "CAM — Late Join Co",
            "template_key": "corp-etb", "created_by": "analyst1",
            "sections": [{"section_code": "exec_summary", "name": "Executive Summary",
                          "order": 1, "content": "summary", "fixed_format": False,
                          "generated": True}]}).json()

        r = c.post(f"/api/cams/{cam['id']}/sections", headers=service, json={
            "section_code": "financial_analysis", "name": "Financial Analysis",
            "order": 2, "content": "late content", "fixed_format": False})
        assert r.status_code == 201 and r.json()["created"] is True

        # idempotent for repeats: same code lands as a new version instead
        r = c.post(f"/api/cams/{cam['id']}/sections", headers=service, json={
            "section_code": "financial_analysis", "name": "Financial Analysis",
            "order": 2, "content": "fresher content", "fixed_format": False})
        assert r.status_code == 201
        assert r.json()["created"] is False and r.json()["version_no"] == 2

        fetched = c.get(f"/api/cams/{cam['id']}", headers=ANALYST).json()
        codes = [s["section_code"] for s in fetched["sections"]]
        assert codes == ["exec_summary", "financial_analysis"]
        fin = fetched["sections"][1]
        assert fin["content"] == "fresher content"

        # end users cannot use the internal path
        assert c.post(f"/api/cams/{cam['id']}/sections", headers=ANALYST,
                      json={"section_code": "x", "content": ""}).status_code == 403

        c.post(f"/api/cams/{cam['id']}/finalise", headers=ANALYST)
        r = c.post(f"/api/cams/{cam['id']}/sections", headers=service, json={
            "section_code": "another", "name": "", "order": 3, "content": "",
            "fixed_format": False})
        assert r.status_code == 409  # finalised CAMs are closed


# ------------------------------------------------------- masters bundle

def test_masters_bundle_roundtrip(admin_headers, admin2_headers, auditor_headers,
                                  analyst_headers, monkeypatch):
    from tests.test_master_config import publish

    payload = {"code": "bundle_demo_type", "name": "Bundle demo type",
               "description": "original", "synonyms": ["bundle demo"],
               "keywords": ["portability"], "active": True}
    with TestClient(mc.app) as c:
        c.post("/api/masters/doctypes", json={"key": "bundle_demo_type",
                                              "payload": payload}, headers=admin_headers)
        publish(c, "doctypes", "bundle_demo_type", admin_headers, admin2_headers)

        # export: business_admin and auditor may, analysts may not
        assert c.get("/api/masters/export-bundle",
                     headers=analyst_headers).status_code == 403
        bundle = c.get("/api/masters/export-bundle", headers=auditor_headers).json()
        entry = next(m for m in bundle["masters"]
                     if m["mtype"] == "doctype" and m["key"] == "bundle_demo_type")
        assert entry["payload"]["description"] == "original"
        assert "tagging_confidence_threshold" in bundle["settings"]

        # re-import identical bundle -> everything unchanged, nothing drafted
        report = c.post("/api/masters/import-bundle",
                        json={"masters": bundle["masters"]}, headers=admin_headers).json()
        assert report["errors"] == [] and report["created"] == []
        assert "doctype:bundle_demo_type" in report["unchanged"]

        # a changed payload imports as a NEW DRAFT under maker-checker
        entry["payload"]["description"] = "changed in target environment"
        report = c.post("/api/masters/import-bundle",
                        json={"masters": [entry]}, headers=admin_headers).json()
        assert len(report["updated"]) == 1
        item = c.get("/api/masters/doctypes/bundle_demo_type", headers=admin_headers).json()
        drafted = item["versions"][-1]
        assert drafted["status"] == "draft" and drafted["change_note"] == "bundle import"
        # published version untouched until a second admin approves
        assert item["published_version"] != drafted["version_no"]

        # unknown master types and invalid payloads land in the error report
        report = c.post("/api/masters/import-bundle", json={"masters": [
            {"mtype": "nonsense", "key": "x", "payload": {}},
            {"mtype": "doctype", "key": "bad", "payload": {"code": "mismatch"}},
        ]}, headers=admin_headers).json()
        assert len(report["errors"]) == 2


def test_cam_delivered_before_run_turns_terminal(wired, analyst_headers, monkeypatch):
    """Atomicity guarantee: a poller that observes a terminal run status must
    also observe cam_id — the CAM is delivered while the run still reads as
    running, then status+cam_id commit together."""
    observed = {}

    def create_cam_spy(payload):
        from cam.services.orchestration.models import Run
        with orch.SessionLocal() as db:
            observed["status_at_handoff"] = db.get(Run, payload["run_id"]).status
        return {"id": "cam-atomic", **payload,
                "sections": [{**s, "id": f"os-{s['section_code']}"}
                             for s in payload["sections"]]}

    monkeypatch.setattr(resolver, "create_cam", create_cam_spy)
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
    assert observed["status_at_handoff"] == "running"
    assert state["status"] == "complete" and state["cam_id"] == "cam-atomic"


# ----------------------------------------------------------- agentic pipeline

def test_agentic_pipeline_order_checks_and_grounding(wired, analyst_headers):
    from tests.test_orchestration import FAKE_FACTS

    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()
    fin = next(s for s in state["sections"] if s["section_code"] == "financial_analysis")

    # canonical agent order, all recorded in the trace with token usage
    assert [t["agent"] for t in fin["agent_trace"]] == [
        "extraction", "summarisation", "materiality", "consistency"]
    assert fin["checks"]["materiality"]["passed"] is True
    assert fin["checks"]["consistency"]["passed"] is True
    assert fin["facts_count"] == len(FAKE_FACTS)
    assert fin["tokens_in"] == 140  # extraction 40 + summarisation 100

    # the extraction agent's facts are the summariser's grounding
    fin_gen = next(p for p in wired["genai"]
                   if p["layers"]["section_prompt"].startswith("Analyse"))
    assert fin_gen["extracted_facts"] == FAKE_FACTS

    # the consistency agent sees other sections' figures (cross-section check)
    cons_payloads = [p for agent, p in wired["agents"] if agent == "consistency"]
    assert any("exec_summary" in (p.get("other_sections") or {}) for p in cons_payloads)


def test_materiality_revision_loop_bounded_and_disclosed(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(resolver, "genai_materiality",
                        lambda payload: {"passed": False,
                                         "omissions": ["Capacity utilisation"],
                                         "flags": [], "notes": "material gap",
                                         "model": "m", "usage": {}})
    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        state = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()

    fin = next(s for s in state["sections"] if s["section_code"] == "financial_analysis")
    mat = fin["checks"]["materiality"]
    assert mat["passed"] is False and mat["revisions"] == 1  # bounded by the limit
    agents_seq = [t["agent"] for t in fin["agent_trace"]]
    assert agents_seq.count("summarisation:revision") == 1
    assert "materiality:recheck" in agents_seq

    # the revision fed the omissions back to the summariser
    revision_calls = [p for p in wired["genai"] if p.get("feedback")]
    assert revision_calls
    assert revision_calls[0]["feedback"]["omissions"] == ["Capacity utilisation"]

    # unresolved omissions are disclosed in the gap trailer (FR-D05)
    trailer = next(s for s in wired["cams"][0]["sections"] if s["section_code"] == "_gaps")
    assert "Materiality-check agent" in trailer["content"]
    assert "Capacity utilisation" in trailer["content"]
