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

    # agentic check outcomes that remained unresolved after bounded revision
    mat_lines, cons_lines, unchecked = [], [], []
    for s in sections:
        checks = s.checks or {}
        materiality = checks.get("materiality") or {}
        consistency = checks.get("consistency") or {}
        if materiality.get("passed") is False:
            for omission in materiality.get("omissions", []):
                mat_lines.append(f"- {s.name}: {omission}")
        if consistency.get("passed") is False:
            for issue in consistency.get("inconsistencies", []):
                cons_lines.append(f"- {s.name}: {issue}")
        for role in ("materiality", "consistency"):
            if checks.get(role, {}).get("passed") is None and checks.get(role):
                unchecked.append(f"- {s.name}: {role} check returned no usable verdict")
    # transparency: external (non-case) intelligence consulted via connectors
    external = []
    for s in sections:
        seen = {str(f.get("source", "")) for f in (s.facts or [])
                if str(f.get("source", "")).startswith(("NEWS:", "SEARCH:"))}
        for src in sorted(seen):
            external.append(f"- {s.name}: {src}")
    if external:
        parts.append("**External intelligence consulted (client-provided connectors, "
                     "verify against primary sources):**\n" + "\n".join(external))

    if mat_lines:
        parts.append("**Materiality-check agent — unresolved material omissions:**\n"
                     + "\n".join(mat_lines))
    if cons_lines:
        parts.append("**Consistency-check agent — unresolved inconsistencies:**\n"
                     + "\n".join(cons_lines))
    if unchecked:
        parts.append("**Checks that could not be completed:**\n" + "\n".join(unchecked))

    if len(parts) == 1:
        parts.append("No data gaps were identified for this generation. All sections "
                     "passed the materiality and consistency check agents.")
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

    # External-intelligence grounding (client-provided connectors, integrated):
    # only when this section's prompt opts in AND the connector is enabled in
    # the run's settings snapshot. fetch_connector_context is fail-open, so a
    # connector outage never blocks the run; disabled connectors add nothing,
    # leaving the pipeline identical to a document-only run.
    pipeline_settings = resolution.get("settings") or {}
    if prompt_payload.get("uses_external_context"):
        industry = resolution.get("industry_name", "")
        if pipeline_settings.get("connectors_news_enabled"):
            grounding += resolver.fetch_connector_context("news", run.borrower_name, industry)
        if pipeline_settings.get("connectors_search_enabled"):
            grounding += resolver.fetch_connector_context("search", run.borrower_name, industry)

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


_NUM_RE = None


def _figures_from_facts(facts: list[dict]) -> list[str]:
    global _NUM_RE
    if _NUM_RE is None:
        import re
        _NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
    figures: set[str] = set()
    for fact in facts or []:
        for token in _NUM_RE.findall(f"{fact.get('value', '')} {fact.get('quote', '')}"):
            figures.add(token.replace(",", ""))
    return sorted(figures)


def _other_sections_digest(run_id: str, exclude_code: str) -> dict[str, list[str]]:
    """Key figures already used by the run's other completed sections — the
    consistency agent's cross-section context."""
    with SessionLocal() as db:
        rows = db.scalars(select(SectionJob).where(
            SectionJob.run_id == run_id, SectionJob.kind == "initial",
            SectionJob.status == "complete",
            SectionJob.section_code != exclude_code)).all()
        return {row.section_code: _figures_from_facts(row.facts)[:8] for row in rows}


def _run_agent_pipeline(run: Run, job: SectionJob) -> dict:
    """Extraction → summarisation → materiality check → consistency check,
    with bounded revision loops (FR-D04 + agentic BRD addendum). Every agent
    call is recorded in the job's trace for the audit trail."""
    base = _section_payload(run, job)
    rules = {role: (entry or {}).get("prompt_text")
             for role, entry in (run.resolution.get("agent_rules") or {}).items()}
    pipeline_settings = run.resolution.get("settings") or {}
    revision_limit = int(pipeline_settings.get("agent_revision_limit", 1))

    trace: list[dict] = []
    totals = {"in": 0, "out": 0}

    def record(agent: str, resp: dict, **extra) -> None:
        usage = resp.get("usage") or {}
        tokens_in = int(usage.get("input_tokens", 0))
        tokens_out = int(usage.get("output_tokens", 0))
        totals["in"] += tokens_in
        totals["out"] += tokens_out
        trace.append({"agent": agent, "model": resp.get("model", ""),
                      "tokens_in": tokens_in, "tokens_out": tokens_out, **extra})

    # 1 — EXTRACTION AGENT (structured, source-attributed facts)
    extraction = resolver.genai_extract({
        "section_prompt": base["layers"]["section_prompt"],
        "grounding_docs": base["grounding_docs"],
        "placeholders": base["placeholders"],
        "agent_rules": rules.get("extraction"),
        "model_overrides": base.get("model_overrides")})
    facts = extraction.get("facts", [])
    record("extraction", extraction, facts=len(facts),
           parse_ok=extraction.get("parse_ok", True))

    # 2 — SUMMARISATION AGENT (drafts from the extracted facts)
    gen_payload = {**base, "extracted_facts": facts,
                   "agent_rules": rules.get("summarisation")}
    generated = resolver.genai_generate(gen_payload)
    content = generated.get("content", "")
    record("summarisation", generated)

    checks: dict[str, dict] = {}
    kpi_block = base["placeholders"].get("industry_kpis", "")
    context = " ".join(str(v) for v in base["placeholders"].values())

    def revise(feedback: dict, trigger: str, revision_no: int) -> None:
        nonlocal content, generated
        generated = resolver.genai_generate({**gen_payload, "feedback": feedback})
        content = generated.get("content", "")
        record("summarisation:revision", generated, trigger=trigger, revision=revision_no)

    # 3 — MATERIALITY CHECK AGENT (bounded revision loop)
    if pipeline_settings.get("agents_materiality_enabled", True):
        verdict = resolver.genai_materiality({
            "draft": content, "facts": facts, "industry_kpis": kpi_block,
            "section_prompt": base["layers"]["section_prompt"],
            "agent_rules": rules.get("materiality")})
        record("materiality", verdict, passed=verdict.get("passed"),
               omissions=len(verdict.get("omissions") or []))
        revisions = 0
        while verdict.get("passed") is False and revisions < revision_limit:
            revisions += 1
            revise({"omissions": verdict.get("omissions", [])}, "materiality", revisions)
            verdict = resolver.genai_materiality({
                "draft": content, "facts": facts, "industry_kpis": kpi_block,
                "section_prompt": base["layers"]["section_prompt"],
                "agent_rules": rules.get("materiality")})
            record("materiality:recheck", verdict, passed=verdict.get("passed"))
        checks["materiality"] = {
            "passed": verdict.get("passed"), "omissions": verdict.get("omissions", []),
            "flags": verdict.get("flags", []), "notes": verdict.get("notes", ""),
            "revisions": revisions}

    # 4 — CONSISTENCY CHECK AGENT (facts + cross-section figures)
    if pipeline_settings.get("agents_consistency_enabled", True):
        digest = _other_sections_digest(run.id, job.section_code)
        cons_payload = {"draft": content, "facts": facts,
                        "context": f"{context} {kpi_block}",
                        "other_sections": digest,
                        "agent_rules": rules.get("consistency")}
        verdict = resolver.genai_consistency(cons_payload)
        record("consistency", verdict, passed=verdict.get("passed"),
               inconsistencies=len(verdict.get("inconsistencies") or []))
        revisions = 0
        while verdict.get("passed") is False and revisions < revision_limit:
            revisions += 1
            revise({"inconsistencies": verdict.get("inconsistencies", [])},
                   "consistency", revisions)
            verdict = resolver.genai_consistency({**cons_payload, "draft": content})
            record("consistency:recheck", verdict, passed=verdict.get("passed"))
        checks["consistency"] = {
            "passed": verdict.get("passed"),
            "inconsistencies": verdict.get("inconsistencies", []),
            "notes": verdict.get("notes", ""), "revisions": revisions}

    return {"content": content, "facts": facts, "checks": checks, "trace": trace,
            "tokens_in": totals["in"], "tokens_out": totals["out"],
            "untraceable": generated.get("untraceable_numbers", []),
            "model": generated.get("model", "unknown")}


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(SectionJob, job_id)
        run = db.get(Run, job.run_id)
    set_correlation_id(run.correlation_id)

    try:
        result = _run_agent_pipeline(run, job)
        with SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.status = "complete"
            job.error = None
            job.content = result["content"]
            job.facts = result["facts"]
            job.checks = result["checks"]
            job.agent_trace = result["trace"]
            job.tokens_in = result["tokens_in"]
            job.tokens_out = result["tokens_out"]
            job.untraceable = result["untraceable"]
            run = db.get(Run, job.run_id)
            if run.model_identity in ("", "pending"):
                run.model_identity = result["model"]
            db.commit()
        audit.emit(settings, action="run.section_completed", entity_type="run_section",
                   entity_id=f"{run.id}:{job.section_code}", case_id=run.case_id,
                   run_id=run.id, detail={"section": job.section_code, "kind": job.kind,
                                          "untraceable": job.untraceable,
                                          "tokens_out": job.tokens_out,
                                          "agents": [t["agent"] for t in job.agent_trace],
                                          "checks": {k: v.get("passed")
                                                     for k, v in job.checks.items()}})
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
                                           for s in sections if s.untraceable},
                           "agent_checks": {s.section_code: {k: v.get("passed")
                                                             for k, v in (s.checks or {}).items()}
                                            for s in sections if s.checks}})


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
