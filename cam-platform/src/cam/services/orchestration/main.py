"""orchestration service — resolves template → prompts → KPIs → documents,
snapshots every master version used, runs the async section queue, and hands
the finished CAM (plus its data-gap trailer) to the output service.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from cam.common import audit
from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.correlation import get_correlation_id
from cam.common.db import make_engine, make_session_factory
from cam.common.errors import ApiError
from cam.common.rbac import is_own_scoped
from cam.common.security import Principal, make_auth_dependencies

from . import resolver, worker
from .models import Base, Run, SectionJob

settings = get_settings("orchestration")
engine = make_engine(settings.resolved_db_url())
SessionLocal = make_session_factory(engine)
current_principal, require, require_service = make_auth_dependencies(settings)

worker.SessionLocal = SessionLocal
worker.settings = settings

app = create_app(settings, "CAM orchestration service")

WORKER_ENABLED = os.environ.get("CAM_WORKER_ENABLED", "true").lower() != "false"
_stop_event: asyncio.Event | None = None
_worker_tasks: list[asyncio.Task] = []


@app.on_event("startup")
async def startup() -> None:
    Base.metadata.create_all(engine)
    if WORKER_ENABLED:
        global _stop_event
        _stop_event = asyncio.Event()
        for i in range(settings.worker_concurrency):
            _worker_tasks.append(asyncio.create_task(worker.worker_loop(_stop_event, i)))


@app.on_event("shutdown")
async def shutdown() -> None:
    if _stop_event:
        _stop_event.set()
        for task in _worker_tasks:
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                task.cancel()


class RunCreate(BaseModel):
    case_id: str
    template_key: str
    preference_override: dict | None = None
    proceed_with_gaps: bool = False


def _case_tags(documents: list[dict]) -> dict[str, list[dict]]:
    """doctype_code -> usable (non-quarantined) documents carrying that tag,
    ordered for grounding (FR-C05 multiplicity with period labels)."""
    by_type: dict[str, list[dict]] = {}
    for doc in documents:
        if doc.get("status") == "quarantined":
            continue
        for tag in doc.get("tags", []):
            label = tag.get("period_label") or doc.get("filename", "")
            by_type.setdefault(tag["doctype_code"], []).append(
                {"doc_id": doc["id"], "doctype_code": tag["doctype_code"],
                 "label": f"{tag['doctype_code']}:{label}",
                 "seq": (tag.get("seq_order") or 0, label)})
    for docs in by_type.values():
        docs.sort(key=lambda d: d.pop("seq"))
    return by_type


@app.post("/api/runs", status_code=202)
def create_run(body: RunCreate, request: Request,
               principal: Principal = Depends(require("generate:run"))):
    case = resolver.fetch_case(body.case_id)
    if is_own_scoped(principal.roles) and case.get("created_by") != principal.username:
        raise ApiError.forbidden("not your case")

    with SessionLocal() as db:
        active = db.scalar(select(func.count()).select_from(Run).where(
            Run.created_by == principal.username, Run.status.in_(["queued", "running"]))) or 0
    if active >= settings.max_active_runs_per_user:
        raise ApiError(429, "rate_limited",
                       f"active-run limit reached ({settings.max_active_runs_per_user}); "
                       "wait for a run to finish (FR-D07)")

    resolved = resolver.fetch_resolved_template(body.template_key)
    kpi = resolver.fetch_kpi_set(case.get("industry_code", "")) if case.get("industry_code") else {}
    documents = resolver.fetch_case_documents(body.case_id)
    tags = _case_tags(documents)

    # FR-C09/FR-D05: completeness against the template's required set
    missing = [code for code in resolved["template"].get("required_doc_types", [])
               if code not in tags]
    if missing and not body.proceed_with_gaps:
        raise ApiError(409, "conflict",
                       "required documents are missing; set proceed_with_gaps to continue",)
    gaps = [{"doctype_code": code, "reason": "required by template, not present on case"}
            for code in missing]

    # applied preference profile (FR-B02): per-run override beats user profile
    if body.preference_override:
        prefs = {**body.preference_override, "source": "override"}
    else:
        profile = resolver.fetch_user_preferences(request.headers.get("Authorization", ""))
        prefs = {k: profile[k] for k in ("tonality", "structure_bias", "table_usage", "length")}
        prefs["source"] = "org_default" if profile.get("scope") == "org_default" else "user"

    master_versions = {
        "template": resolved["template_version"],
        "prompts": {s["section_code"]: s["prompt"]["version"] for s in resolved["sections"]},
        "global_rules": (resolved.get("global_rules") or {}).get("version"),
        "doctypes": resolved.get("doctype_master_versions", {}),
        "kpi_set": kpi.get("kpi_set_version"),
    }
    resolution = {
        "template": resolved["template"], "sections": resolved["sections"],
        "global_rules": resolved.get("global_rules"),
        "kpis": kpi.get("kpis", []),
        "industry_name": (kpi.get("industry") or {}).get("industry_name", ""),
        "case": {"segment": case.get("segment", ""), "relationship": case.get("relationship", "")},
    }

    with SessionLocal() as db:
        run = Run(case_id=body.case_id, template_key=body.template_key,
                  created_by=principal.username, correlation_id=get_correlation_id(),
                  borrower_name=case.get("borrower_name", ""),
                  applied_preferences=prefs, master_versions=master_versions,
                  resolution=resolution, gaps=gaps, proceed_with_gaps=body.proceed_with_gaps)
        db.add(run)
        db.flush()  # populate run.id before the section rows reference it
        sections = []
        for s in resolved["sections"]:
            include_if = s.get("include_if_doctype")
            skipped = bool(include_if) and include_if not in tags and not s.get("mandatory")
            input_docs = []
            for code in s["prompt"]["payload"].get("source_doc_types", []):
                input_docs += tags.get(code, [])
            job = SectionJob(
                run_id=run.id, section_code=s["section_code"],
                name=s["prompt"]["payload"].get("section_name", s["section_code"]),
                order_no=s["order"], prompt_version=s["prompt"]["version"],
                fixed_format=bool(s.get("fixed_format")),
                length_guidance=s.get("length_guidance") or "",
                input_docs=input_docs,
                status="skipped" if skipped else "queued",
                skip_reason=(f"conditional section: no '{include_if}' document on the case"
                             if skipped else None))
            db.add(job)
            sections.append(job)
        db.commit()
        run_dict = run.to_dict(sections)

    audit.emit(settings, action="run.started", entity_type="run", entity_id=run_dict["id"],
               principal=principal, case_id=body.case_id, run_id=run_dict["id"],
               detail={"template_key": body.template_key, "master_versions": master_versions,
                       "applied_preferences": prefs, "gaps": gaps,
                       "sections": [s["section_code"] for s in run_dict["sections"]]})
    return run_dict


def _load_run(db, run_id: str, principal: Principal) -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise ApiError.not_found("run")
    if is_own_scoped(principal.roles) and run.created_by != principal.username \
            and not principal.is_service:
        raise ApiError.forbidden("not your run")
    return run


@app.get("/api/runs/usage/summary")
def usage_summary(principal: Principal = Depends(require("audit:read"))):
    if is_own_scoped(principal.roles):
        raise ApiError.forbidden("usage summary is for admin/audit roles")
    with SessionLocal() as db:
        runs = db.scalar(select(func.count()).select_from(Run)) or 0
        jobs = list(db.scalars(select(SectionJob)).all())
    return {"runs": runs, "sections": len(jobs),
            "tokens_in": sum(j.tokens_in for j in jobs),
            "tokens_out": sum(j.tokens_out for j in jobs),
            "retries": sum(max(0, j.attempts - 1) for j in jobs if j.kind == "initial"),
            "regenerations": sum(1 for j in jobs if j.kind == "regeneration"),
            "failed_sections": sum(1 for j in jobs if j.status == "failed")}


@app.get("/api/runs")
def list_runs(case_id: str | None = None,
              principal: Principal = Depends(require("generate:run", "case:read"))):
    with SessionLocal() as db:
        q = select(Run).order_by(Run.created_at.desc())
        if case_id:
            q = q.where(Run.case_id == case_id)
        if is_own_scoped(principal.roles):
            q = q.where(Run.created_by == principal.username)
        runs = db.scalars(q.limit(100)).all()
        out = []
        for run in runs:
            sections = list(db.scalars(select(SectionJob).where(SectionJob.run_id == run.id)).all())
            out.append(run.to_dict(sections))
        return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, principal: Principal = Depends(current_principal)):
    with SessionLocal() as db:
        run = _load_run(db, run_id, principal)
        sections = list(db.scalars(select(SectionJob).where(SectionJob.run_id == run.id)).all())
        return run.to_dict(sections)


@app.post("/api/runs/{run_id}/sections/{section_code}/retry", status_code=202)
def retry_section(run_id: str, section_code: str,
                  principal: Principal = Depends(require("generate:run"))):
    with SessionLocal() as db:
        run = _load_run(db, run_id, principal)
        job = db.scalar(select(SectionJob).where(
            SectionJob.run_id == run_id, SectionJob.section_code == section_code,
            SectionJob.kind == "initial"))
        if not job:
            raise ApiError.not_found("section")
        if job.status != "failed":
            raise ApiError.conflict(f"only failed sections can be retried (status={job.status})")
        job.status = "queued"
        job.error = None
        if run.status in ("partial", "failed"):
            run.status = "running"
        db.commit()
    audit.emit(settings, action="run.section_retried", entity_type="run_section",
               entity_id=f"{run_id}:{section_code}", principal=principal, run_id=run_id,
               detail={"section": section_code})
    return {"status": "queued"}


@app.post("/api/runs/{run_id}/sections/{section_code}/regenerate", status_code=202)
def regenerate_section(run_id: str, section_code: str,
                       principal: Principal = Depends(require("generate:run"))):
    """FR-D06: regenerate one section; the fresh draft lands in the output
    service as a NEW version of that section only."""
    with SessionLocal() as db:
        run = _load_run(db, run_id, principal)
        if not run.cam_id:
            raise ApiError.conflict("run has no CAM yet; retry/complete the run first")
        source = db.scalar(select(SectionJob).where(
            SectionJob.run_id == run_id, SectionJob.section_code == section_code,
            SectionJob.kind == "initial"))
        if not source:
            raise ApiError.not_found("section")
        clone = SectionJob(
            run_id=run_id, section_code=source.section_code, name=source.name,
            order_no=source.order_no, kind="regeneration",
            prompt_version=source.prompt_version, fixed_format=source.fixed_format,
            length_guidance=source.length_guidance, input_docs=source.input_docs)
        db.add(clone)
        db.commit()
    return {"status": "queued"}
