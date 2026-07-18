"""Generation worker: claims queued SectionJobs from the DB-backed queue and
executes them (FR-D01/D02). Runs as in-process asyncio workers by default;
the claim step is serialised so multiple workers (or processes pointed at
PostgreSQL) never double-process a job. Failed sections stay individually
retryable without a full re-run.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import timedelta

from sqlalchemy import select

from cam.common import audit
from cam.common.correlation import set_correlation_id
from cam.common.db import utcnow

from . import resolver
from .models import Run, SectionJob

log = logging.getLogger("cam.orchestration.worker")

_claim_lock = threading.Lock()
_finalize_lock = threading.Lock()

# bound at import time by main.py (single SessionLocal for service + worker)
SessionLocal = None
settings = None

# Recovery policy: a job claimed longer than the lease with no terminal state
# means its worker died mid-flight. It is re-queued until the attempt cap,
# then failed loudly (visible on the run, retryable by the analyst).
JOB_LEASE_SECONDS = int(os.environ.get("CAM_JOB_LEASE_SECONDS", "600"))
MAX_SECTION_ATTEMPTS = int(os.environ.get("CAM_MAX_SECTION_ATTEMPTS", "3"))
_REAP_INTERVAL_SECONDS = 30.0
_last_reap = 0.0


def render_kpi_block(kpis: list[dict], section_code: str) -> str:
    """FR-A11: the {{industry_kpis}} injection block for one section."""
    lines = []
    for kpi in kpis:
        applicable = kpi.get("sections") or []
        if applicable and section_code not in applicable:
            continue
        polarity = "higher is better" if kpi.get("polarity") == "higher_better" else "lower is better"
        benchmark = f"; benchmark {kpi['benchmark']}" if kpi.get("benchmark") else ""
        definition = f" — {kpi['definition']}" if kpi.get("definition") else ""
        lines.append(f"- {kpi['name']} ({kpi.get('unit', 'n/a')}, {polarity}{benchmark}){definition}")
    return "\n".join(lines) if lines else "(no industry KPIs configured for this section)"


def build_gap_trailer(run: Run, sections: list[SectionJob]) -> str:
    """FR-D05: structured disclosure of everything missing or unusable —
    appended to the CAM instead of silent omission."""
    parts = ["*This trailer is generated automatically and discloses the inputs that were "
             "missing or unusable for this AI-assisted draft.*"]
    if run.gaps:
        parts.append("**Missing required documents (analyst chose to proceed):**\n"
                     + "\n".join(f"- `{g['doctype_code']}` — {g['reason']}" for g in run.gaps))
    skipped = [s for s in sections if s.status == "skipped"]
    if skipped:
        parts.append("**Sections skipped:**\n"
                     + "\n".join(f"- {s.name}: {s.skip_reason}" for s in skipped))
    failed = [s for s in sections if s.status == "failed"]
    if failed:
        parts.append("**Sections that failed to generate:**\n"
                     + "\n".join(f"- {s.name}: {s.error}" for s in failed))
    flagged = [(s.name, s.untraceable) for s in sections if s.untraceable]
    if flagged:
        lines = [f"- {name}: {', '.join(nums)}" for name, nums in flagged]
        parts.append("**Figures that could not be traced to a supplied source (verify "
                     "before finalising):**\n" + "\n".join(lines))
    if len(parts) == 1:
        parts.append("No data gaps were identified for this generation.")
    return "\n\n".join(parts)


def _claim_next() -> str | None:
    """Claim one queued job whose run is still active. Serialised claim keeps
    this correct for in-process concurrency; on PostgreSQL the same query runs
    with FOR UPDATE SKIP LOCKED semantics (see ADR-0004)."""
    from sqlalchemy import or_

    with _claim_lock, SessionLocal() as db:
        job = db.scalar(
            select(SectionJob).join(Run, SectionJob.run_id == Run.id)
            .where(SectionJob.status == "queued",
                   or_(Run.status.in_(["queued", "running"]),
                       SectionJob.kind == "regeneration"))
            .order_by(SectionJob.order_no).limit(1))
        if not job:
            return None
        job.status = "running"
        job.attempts += 1
        job.claimed_at = utcnow()
        run = db.get(Run, job.run_id)
        if run.status == "queued":
            run.status = "running"
        db.commit()
        return job.id


def reap_stuck_jobs() -> int:
    """Requeue (or fail, past the attempt cap) jobs whose worker died holding
    the claim. Returns the number of jobs touched."""
    cutoff = utcnow() - timedelta(seconds=JOB_LEASE_SECONDS)
    touched: list[str] = []
    with _claim_lock, SessionLocal() as db:
        stuck = list(db.scalars(select(SectionJob).where(
            SectionJob.status == "running", SectionJob.claimed_at.isnot(None),
            SectionJob.claimed_at < cutoff)).all())
        for job in stuck:
            if job.attempts >= MAX_SECTION_ATTEMPTS:
                job.status = "failed"
                job.error = (f"worker lost after {job.attempts} attempt(s); "
                             f"lease of {JOB_LEASE_SECONDS}s expired")
            else:
                job.status = "queued"
                job.claimed_at = None
            touched.append(job.id)
        db.commit()
        failed_ids = [j.id for j in stuck if j.status == "failed"]
    for job_id in touched:
        log.warning("reaper recovered stuck section job %s", job_id)
    for job_id in failed_ids:
        # a terminal failure may complete its run — settle it
        _after_section(job_id)
    return len(touched)


def _maybe_reap() -> None:
    global _last_reap
    now = time.monotonic()
    if now - _last_reap >= _REAP_INTERVAL_SECONDS:
        _last_reap = now
        try:
            reap_stuck_jobs()
        except Exception:  # pragma: no cover - defensive
            log.exception("reaper sweep failed")


def _section_payload(run: Run, job: SectionJob) -> dict:
    resolution = run.resolution
    section = next(s for s in resolution["sections"] if s["section_code"] == job.section_code)
    prompt_payload = section["prompt"]["payload"]

    kpi_block = ""
    if prompt_payload.get("uses_industry_kpis"):
        kpi_block = render_kpi_block(resolution.get("kpis", []), job.section_code)

    placeholders = {
        "borrower_name": run.borrower_name,
        "case_type": resolution.get("case", {}).get("segment", ""),
        "relationship": resolution.get("case", {}).get("relationship", ""),
        "industry_name": resolution.get("industry_name", ""),
        "industry_kpis": kpi_block,
        "today": utcnow().strftime("%Y-%m-%d"),
    }
    from cam.common.placeholders import resolve_placeholders
    section_prompt, _ = resolve_placeholders(prompt_payload["prompt_text"], placeholders)

    grounding = []
    for ref in job.input_docs:
        # FR-D03: only THIS section's mapped documents are fetched — no bleed
        text = resolver.fetch_document_text(ref["doc_id"])
        grounding.append({"doctype_code": ref["doctype_code"], "label": ref["label"],
                          "text": text})

    global_rules = (resolution.get("global_rules") or {}).get("prompt_text")
    return {
        "mode": "section",
        "layers": {"global_rules": global_rules,
                   "template_instructions": resolution["template"].get("template_instructions"),
                   "section_prompt": section_prompt},
        "placeholders": placeholders,
        "grounding_docs": grounding,
        "preferences": None if job.fixed_format else run.applied_preferences,
        "fixed_format": job.fixed_format,
        "length_guidance": job.length_guidance or None,
        "model_overrides": prompt_payload.get("model_overrides"),
    }


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(SectionJob, job_id)
        run = db.get(Run, job.run_id)
    set_correlation_id(run.correlation_id)

    try:
        payload = _section_payload(run, job)
        result = resolver.genai_generate(payload)
        with SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.status = "complete"
            job.error = None
            job.content = result.get("content", "")
            usage = result.get("usage", {})
            job.tokens_in = int(usage.get("input_tokens", 0))
            job.tokens_out = int(usage.get("output_tokens", 0))
            job.untraceable = result.get("untraceable_numbers", [])
            run = db.get(Run, job.run_id)
            if run.model_identity in ("", "pending"):
                run.model_identity = result.get("model", "unknown")
            db.commit()
        audit.emit(settings, action="run.section_completed", entity_type="run_section",
                   entity_id=f"{run.id}:{job.section_code}", case_id=run.case_id,
                   run_id=run.id, detail={"section": job.section_code, "kind": job.kind,
                                          "untraceable": job.untraceable,
                                          "tokens_out": job.tokens_out})
    except Exception as exc:
        log.exception("section %s of run %s failed", job.section_code, job.run_id)
        with SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.status = "failed"
            job.error = str(exc)[:1000]
            db.commit()
        audit.emit(settings, action="run.section_failed", entity_type="run_section",
                   entity_id=f"{job.run_id}:{job.section_code}", run_id=job.run_id,
                   detail={"section": job.section_code, "error": str(exc)[:300]})

    _after_section(job_id)


def _after_section(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(SectionJob, job_id)
        run = db.get(Run, job.run_id)

    if job.kind == "regeneration" or (run.cam_id and job.status == "complete"):
        # a CAM already exists — the fresh draft joins it: as a new version of
        # the matching section, or as a late-arriving section (a retried
        # failure was never part of the original handoff)
        if run.cam_id and job.status == "complete":
            try:
                cam = resolver.fetch_cam(run.cam_id)
                match = next((s for s in cam["sections"]
                              if s["section_code"] == job.section_code), None)
                if match:
                    resolver.push_section_version(run.cam_id, match["id"], job.content)
                else:
                    resolver.create_cam_section(run.cam_id, {
                        "section_code": job.section_code, "name": job.name,
                        "order": job.order_no, "content": job.content or "",
                        "fixed_format": job.fixed_format})
                audit.emit(settings, action="run.section_regenerated",
                           entity_type="run_section",
                           entity_id=f"{run.id}:{job.section_code}",
                           case_id=run.case_id, run_id=run.id, cam_id=run.cam_id,
                           detail={"section": job.section_code,
                                   "late_join": match is None})
            except Exception:
                log.exception("failed to push regenerated section to output service")
        if job.kind == "initial":
            # a retried section still settles its run's status (running -> a
            # terminal state) even though the CAM handoff already happened
            _maybe_finalize(run.id)
        return

    _maybe_finalize(run.id)


def _maybe_finalize(run_id: str) -> None:
    """When every initial section is terminal, settle the run status and hand
    the CAM (with its gap trailer) to the output service exactly once."""
    with _finalize_lock:
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            if run.status not in ("queued", "running"):
                return
            sections = list(db.scalars(select(SectionJob).where(
                SectionJob.run_id == run_id, SectionJob.kind == "initial")).all())
            if any(s.status in ("queued", "running") for s in sections):
                return
            complete = [s for s in sections if s.status == "complete"]
            failed = [s for s in sections if s.status == "failed"]
            new_status = "failed" if not complete else ("partial" if failed else "complete")
            if not complete or run.cam_id:
                # no CAM handoff to sequence — commit the terminal status now
                run.status = new_status
                db.commit()

        if not complete:
            audit.emit(settings, action="run.completed", entity_type="run", entity_id=run.id,
                       case_id=run.case_id, run_id=run.id,
                       detail={"status": "failed", "master_versions": run.master_versions})
            resolver.update_case_status(run.case_id, "open")
            return

        if run.cam_id:
            # late settle after a retry — the CAM was already delivered; only
            # the run status needed updating (partial -> complete, etc.)
            return

        cam_sections = [{"section_code": s.section_code, "name": s.name, "order": s.order_no,
                         "content": s.content or "", "fixed_format": s.fixed_format,
                         "generated": True} for s in sorted(complete, key=lambda x: x.order_no)]
        cam_sections.append({"section_code": "_gaps", "name": "Data Gaps & Disclosures",
                             "order": 9999, "content": build_gap_trailer(run, sections),
                             "fixed_format": True, "generated": True})
        # Deliver the CAM BEFORE the run turns terminal: a poller that sees a
        # terminal run status must be able to rely on cam_id being present.
        cam_id = None
        try:
            cam = resolver.create_cam({
                "case_id": run.case_id, "run_id": run.id,
                "title": f"CAM — {run.borrower_name}",
                "template_key": run.template_key, "created_by": run.created_by,
                "sections": cam_sections,
            })
            cam_id = cam["id"]
        except Exception:
            log.exception("CAM handoff to output service failed for run %s", run_id)
        with SessionLocal() as db:
            fresh = db.get(Run, run_id)
            fresh.status = new_status
            if cam_id:
                fresh.cam_id = cam_id
            db.commit()
        run.status = new_status
        run.cam_id = cam_id
        if cam_id:
            resolver.update_case_status(run.case_id, "drafted")

        audit.emit(settings, action="run.completed", entity_type="run", entity_id=run.id,
                   case_id=run.case_id, run_id=run.id, cam_id=run.cam_id,
                   detail={"status": run.status,
                           "master_versions": run.master_versions,
                           "model_identity": run.model_identity,
                           "applied_preferences": run.applied_preferences,
                           "gaps": run.gaps,
                           "input_documents": {s.section_code: s.input_docs for s in sections},
                           "untraceable": {s.section_code: s.untraceable
                                           for s in sections if s.untraceable}})


def process_next() -> bool:
    job_id = _claim_next()
    if not job_id:
        return False
    process_job(job_id)
    return True


def drain(max_jobs: int = 200) -> int:
    """Synchronous queue drain — used by tests and available for CLI ops."""
    n = 0
    while n < max_jobs and process_next():
        n += 1
    return n


async def worker_loop(stop: asyncio.Event, worker_no: int) -> None:
    log.info("generation worker %d started", worker_no)
    while not stop.is_set():
        try:
            if worker_no == 0:
                await asyncio.to_thread(_maybe_reap)
            worked = await asyncio.to_thread(process_next)
        except Exception:
            log.exception("worker %d crashed on a job; continuing", worker_no)
            worked = False
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
    log.info("generation worker %d stopped", worker_no)
