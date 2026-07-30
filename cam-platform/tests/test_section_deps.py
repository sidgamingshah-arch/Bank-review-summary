"""Section interlinking (FR-D08): template dependency validation (acyclic DAG),
the worker's dependency-gated claim, and injection of a dependency section's
drafted OUTPUT as grounding for the dependent section (the executive-summary
consumes-everything case)."""
from __future__ import annotations

import copy

from fastapi.testclient import TestClient

import cam.services.orchestration.main as orch
from cam.services.master_config import schemas
from cam.services.orchestration import worker

# registers the shared orchestration fakes as fixtures
from tests.test_orchestration import (  # noqa: F401
    RESOLVED_TEMPLATE, _create_run, wired)


# --------------------------------------------------------------- validation
def _tmpl_errors(sections):
    payload = {"name": "Corp CAM", "segment": "corporate", "relationship": "etb",
               "sections": sections, "required_doc_types": []}
    codes = {s["section_code"] for s in sections}
    _, errors = schemas.validate_payload("template", "tmpl", payload,
                                         doctype_codes=set(), prompt_keys=codes,
                                         industry_codes=set())
    return errors


def test_dependency_cycle_rejected():
    errs = _tmpl_errors([
        {"order": 1, "section_code": "a", "depends_on": ["b"]},
        {"order": 2, "section_code": "b", "depends_on": ["a"]}])
    assert any("cycle" in e for e in errs)


def test_self_dependency_rejected():
    errs = _tmpl_errors([{"order": 1, "section_code": "a", "depends_on": ["a"]}])
    assert any("cannot depend on itself" in e for e in errs)


def test_unknown_dependency_rejected():
    errs = _tmpl_errors([
        {"order": 1, "section_code": "a", "depends_on": ["ghost"]},
        {"order": 2, "section_code": "b"}])
    assert any("unknown section 'ghost'" in e for e in errs)


def test_depends_on_all_is_acyclic():
    errs = _tmpl_errors([
        {"order": 1, "section_code": "exec", "depends_on_all": True},
        {"order": 2, "section_code": "a"},
        {"order": 3, "section_code": "b"}])
    assert not any("cycle" in e for e in errs)


def test_depends_on_all_cycle_rejected():
    # exec depends on everything; a depends back on exec -> cycle
    errs = _tmpl_errors([
        {"order": 1, "section_code": "exec", "depends_on_all": True},
        {"order": 2, "section_code": "a", "depends_on": ["exec"]}])
    assert any("cycle" in e for e in errs)


# --------------------------------------------------------------- claim gate
def test_deps_satisfied_helper():
    assert worker._deps_satisfied({"a": "complete", "b": "skipped"}, ["a", "b"])
    assert worker._deps_satisfied({"a": "failed"}, ["a"])          # terminal, not blocking
    assert not worker._deps_satisfied({"a": "running"}, ["a"])     # still in flight
    assert not worker._deps_satisfied({"a": "queued"}, ["a"])
    assert worker._deps_satisfied({}, ["ghost"])                   # unknown never blocks
    assert worker._deps_satisfied({"a": "complete"}, [])           # no deps


# --------------------------------------------------------------- integration
def _template_with_exec_consumes_all():
    tmpl = copy.deepcopy(RESOLVED_TEMPLATE)
    for s in tmpl["sections"]:
        if s["section_code"] == "exec_summary":
            s["depends_on_all"] = True
    return tmpl


def test_exec_summary_waits_for_and_consumes_other_sections(wired, analyst_headers, monkeypatch):
    monkeypatch.setattr(orch.resolver, "fetch_resolved_template",
                        lambda k: _template_with_exec_consumes_all())

    with TestClient(orch.app) as c:
        r = _create_run(c, analyst_headers)
        assert r.status_code == 202, r.text
        run = r.json()
        by_code = {s["section_code"]: s for s in run["sections"]}
        # depends_on_all expanded to every other section (project_review is skipped
        # but still a declared dependency)
        assert set(by_code["exec_summary"]["depends_on"]) == {"financial_analysis",
                                                              "project_review"}
        assert worker.drain() >= 2

    # ordering: the dependency (financial_analysis) is drafted before exec_summary
    prompts = [p["layers"]["section_prompt"] for p in wired["genai"]]
    fin_idx = next(i for i, p in enumerate(prompts) if p.startswith("Analyse"))
    exec_idx = next(i for i, p in enumerate(prompts) if p.startswith("Summarise"))
    assert fin_idx < exec_idx

    # exec_summary is grounded on the OUTPUT of the section it depends on
    exec_payload = next(p for p in wired["genai"]
                        if p["layers"]["section_prompt"].startswith("Summarise"))
    section_outputs = [g for g in exec_payload["grounding_docs"]
                       if g["doctype_code"] == "section_output"]
    assert section_outputs, "exec summary should receive dependency output as grounding"
    assert any("Draft for" in g["text"] for g in section_outputs)
