"""Post-generation consistency (consistency_scope='post_generation'): the
per-section pipeline defers the consistency check; after every section is drafted
one memo-level reconcile pass sees them all and re-drafts ONLY the sections it
flags. Fail-open: a reconcile failure still finalises the run."""
from __future__ import annotations

import copy

from fastapi.testclient import TestClient

import cam.services.genai.main as genai
import cam.services.orchestration.main as orch
from cam.services.orchestration import worker
from tests.conftest import make_service_headers

# registers the shared orchestration fakes as fixtures
from tests.test_orchestration import (  # noqa: F401
    RESOLVED_TEMPLATE, _create_run, wired)


def _post_gen_template():
    tmpl = copy.deepcopy(RESOLVED_TEMPLATE)
    tmpl["settings"] = {"tagging_confidence_threshold": 0.55,
                        "consistency_scope": "post_generation"}
    return tmpl


def test_post_generation_defers_consistency_and_reconciles(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(orch.resolver, "fetch_resolved_template", lambda k: _post_gen_template())

    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        assert worker.drain() == 3  # 2 sections + 1 reconcile phase (project_review skipped)
        fetched = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()

    agents = [a for a, _ in wired["agents"]]
    assert "reconcile" in agents           # memo-level pass ran
    assert "consistency" not in agents     # per-section consistency was deferred
    # the internal reconcile phase is not surfaced as a memo section
    assert all(s["section_code"] != "_reconcile" for s in fetched["sections"])
    # every drafted section carries a post-generation consistency verdict
    for code in ("exec_summary", "financial_analysis"):
        chk = {s["section_code"]: s for s in fetched["sections"]}[code]["checks"]["consistency"]
        assert chk["scope"] == "post_generation" and chk["passed"] is True
    assert wired["cams"], "CAM handed off after reconcile"


def test_reconcile_revises_only_flagged_sections(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(orch.resolver, "fetch_resolved_template", lambda k: _post_gen_template())

    def flagging_reconcile(payload):
        out = []
        for s in payload["sections"]:
            bad = s["section_code"] == "financial_analysis"
            out.append({"section_code": s["section_code"], "consistent": not bad,
                        "issues": ["revenue figure conflicts with exec_summary"] if bad else [],
                        "guidance": "Align revenue with the executive summary." if bad else ""})
        return {"sections": out, "parse_ok": True, "notes": "one conflict",
                "model": "m", "usage": {"input_tokens": 5, "output_tokens": 2}}

    def revising_generate(payload):
        wired["genai"].append(payload)
        if payload.get("feedback"):
            return {"content": "REVISED per reconcile — Rs. 4,210 Cr.", "model": "m",
                    "usage": {"input_tokens": 1, "output_tokens": 1}, "untraceable_numbers": []}
        return {"content": f"Draft for {payload['layers']['section_prompt'][:40]} — Rs. 4,210 Cr.",
                "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1},
                "untraceable_numbers": []}

    monkeypatch.setattr(orch.resolver, "genai_reconcile", flagging_reconcile)
    monkeypatch.setattr(orch.resolver, "genai_generate", revising_generate)

    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        fetched = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()

    cam_sections = {s["section_code"]: s for s in wired["cams"][-1]["sections"]}
    assert cam_sections["financial_analysis"]["content"].startswith("REVISED per reconcile")
    assert cam_sections["exec_summary"]["content"].startswith("Draft for")  # untouched

    secs = {s["section_code"]: s for s in fetched["sections"]}
    fin_trace = [t["agent"] for t in secs["financial_analysis"]["agent_trace"]]
    assert "summarisation:reconcile" in fin_trace
    assert secs["financial_analysis"]["checks"]["consistency"]["revisions"] == 1
    # the untouched section had no reconcile re-draft
    assert "summarisation:reconcile" not in [t["agent"] for t in secs["exec_summary"]["agent_trace"]]


def test_reconcile_failure_still_finalizes(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(orch.resolver, "fetch_resolved_template", lambda k: _post_gen_template())

    def boom(_payload):
        raise RuntimeError("reconcile endpoint down")

    monkeypatch.setattr(orch.resolver, "genai_reconcile", boom)

    with TestClient(orch.app) as c:
        run = _create_run(c, analyst_headers).json()
        worker.drain()
        fetched = c.get(f"/api/runs/{run['id']}", headers=analyst_headers).json()

    # fail-open: the memo is still assembled and handed off despite the reconcile error
    assert wired["cams"], "CAM must still be delivered when reconcile fails"
    assert fetched["status"] in ("complete", "partial")


# ------------------------------------------------------------- genai endpoint
def test_reconcile_endpoint_returns_verdict_per_section():
    with TestClient(genai.app) as c:
        r = c.post("/api/genai/reconcile", headers=make_service_headers(), json={"sections": [
            {"section_code": "a", "name": "A", "content": "Revenue Rs 10 Cr.", "figures": ["10"]},
            {"section_code": "b", "name": "B", "content": "Debt Rs 4 Cr.", "figures": ["4"]}]})
        assert r.status_code == 200, r.text
        body = r.json()
    assert {s["section_code"] for s in body["sections"]} == {"a", "b"}
    assert all(s["consistent"] for s in body["sections"])  # mock provider: all consistent
