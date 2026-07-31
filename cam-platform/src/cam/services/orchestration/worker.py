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

# Terminal SectionJob states (a dependency/section is "settled" in any of these).
_TERMINAL = ("complete", "failed", "skipped")

# A dependent section is grounded on the OUTPUT of the sections it depends on;
# cap each injected section so an exec-summary-consumes-all case stays bounded.
_DEP_CONTENT_CAP = 30_000

# Runtime active-concurrency gate. The worker pool is spawned at a fixed ceiling
# (worker_pool_size); the ACTIVE concurrency is the live 'worker_concurrency'
# master setting, cached briefly and clamped to [1, ceiling]. Workers whose index
# is >= active idle, so an admin can dial concurrency up/down without a restart.
_pool_size = 0  # set by main.py at startup
_active_cache: dict = {"value": None, "at": 0.0}
_ACTIVE_TTL_SECONDS = 5.0


def _active_concurrency() -> int:
    """Live active-section concurrency (master setting 'worker_concurrency'),
    cached for a few seconds and clamped to [1, pool ceiling]. Fail-open to the
    environment default when master-config is briefly unreachable."""
    default = int(getattr(settings, "worker_concurrency", 2) or 2)
    ceiling = _pool_size or default
    now = time.monotonic()
    if _active_cache["value"] is not None and now - _active_cache["at"] < _ACTIVE_TTL_SECONDS:
        return _active_cache["value"]
    val = default
    try:
        raw = resolver.fetch_settings().get("worker_concurrency", default)
        val = int(raw)
    except Exception:  # fail-open: a bad/unreachable setting never stalls generation
        val = default
    val = max(1, min(val, ceiling))
    _active_cache["value"] = val
    _active_cache["at"] = now
    return val


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
    # transparency (FR-D05): external (non-case) intelligence consulted via
    # connectors — read deterministically from the run's snapshot (what the
    # worker actually fetched), not scraped from model-written fact sources.
    connector_context = (run.resolution or {}).get("connector_context") or {}
    external = sorted({str(d.get("label", kind)) for kind, docs in connector_context.items()
                       for d in (docs or [])})
    if external:
        parts.append("**External intelligence consulted (client-provided connectors, "
                     "verify against primary sources):**\n"
                     + "\n".join(f"- {label}" for label in external))

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


def _deps_satisfied(statuses: dict[str, str], deps: list) -> bool:
    """True when every declared dependency section is terminal (FR-D08). An
    unknown code (e.g. a section absent from this run) never blocks."""
    return all(statuses.get(code, "complete") in _TERMINAL for code in (deps or []))


def _claim_next() -> str | None:
    """Claim the first ready queued job. A job is ready when its run is active and
    either (a) it is a section whose dependency sections are all terminal (FR-D08),
    or (b) it is the memo-level reconcile phase and every initial section of its run
    is terminal. Serialised claim keeps this correct for in-process concurrency; on
    PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED keeps multi-process claims disjoint
    (see ADR-0004). Ordering by order_no preserves the original claim preference."""
    from sqlalchemy import or_

    with _claim_lock, SessionLocal() as db:
        candidates = list(db.scalars(
            select(SectionJob).join(Run, SectionJob.run_id == Run.id)
            .where(SectionJob.status == "queued",
                   or_(Run.status.in_(["queued", "running"]),
                       SectionJob.kind.in_(["regeneration", "reconcile"])))
            .order_by(SectionJob.order_no)
            .with_for_update(skip_locked=True, of=SectionJob).limit(200)).all())
        if not candidates:
            return None

        # per-run map of initial-section statuses, built once and reused
        status_cache: dict[str, dict[str, str]] = {}

        def statuses_for(run_id: str) -> dict[str, str]:
            if run_id not in status_cache:
                rows = db.scalars(select(SectionJob).where(
                    SectionJob.run_id == run_id, SectionJob.kind == "initial")).all()
                status_cache[run_id] = {r.section_code: r.status for r in rows}
            return status_cache[run_id]

        job = None
        for cand in candidates:
            if cand.kind == "reconcile":
                initial = statuses_for(cand.run_id)
                if initial and all(s in _TERMINAL for s in initial.values()):
                    job = cand
                    break
                continue
            if _deps_satisfied(statuses_for(cand.run_id), cand.depends_on):
                job = cand
                break
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


def _resolve_rag_mode(settings_snap: dict) -> str:
    """Retrieval mode from the run's settings snapshot: rag_mode
    (off|keyword|embedding) wins; the legacy rag_enabled maps True -> embedding."""
    mode = settings_snap.get("rag_mode")
    if mode in ("off", "keyword", "embedding"):
        return mode
    return "embedding" if settings_snap.get("rag_enabled") else "off"


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

    settings_snap = resolution.get("settings") or {}
    rag_mode = _resolve_rag_mode(settings_snap)  # off | keyword | embedding
    rag_top_k = int(settings_snap.get("rag_top_k", 6) or 6)

    # Large-document retrieval (RAG): ground each mapped document on its most
    # relevant passages for this section (query = the resolved section prompt)
    # instead of the full extract. 'embedding' ranks by vector similarity,
    # 'keyword' by lexical overlap (no embedding model). One call ranks each
    # document independently (no bleed, FR-D03). Fail-open per document: anything
    # not retrieved falls back to full-text grounding, so a run never loses a
    # source because retrieval was unavailable.
    hits_by_doc: dict[str, list[dict]] = {}
    if rag_mode != "off" and job.input_docs:
        retrieved = resolver.retrieve_chunks(
            [ref["doc_id"] for ref in job.input_docs], section_prompt, rag_top_k, rag_mode)
        for entry in (retrieved.get("results") or []):
            hits_by_doc[entry.get("doc_id")] = entry.get("chunks") or []

    grounding = []
    retrieval_prov: list[dict] = []
    for ref in job.input_docs:
        # FR-D03: only THIS section's mapped documents are used — no bleed
        chunks = hits_by_doc.get(ref["doc_id"])
        if rag_mode != "off" and chunks:
            passages = "\n\n".join(
                f"[passage {c.get('ordinal')}] {c.get('text', '')}" for c in chunks)
            grounding.append({
                "doctype_code": ref["doctype_code"],
                "label": f"{ref['label']} · {len(chunks)} retrieved passage(s)",
                "text": passages})
            retrieval_prov.append({
                "doc_id": ref["doc_id"], "label": ref["label"], "fallback": False,
                "passages": [{"ordinal": c.get("ordinal"), "score": c.get("score")}
                             for c in chunks]})
        else:
            text = resolver.fetch_document_text(ref["doc_id"])
            grounding.append({"doctype_code": ref["doctype_code"], "label": ref["label"],
                              "text": text})
            if rag_mode != "off":
                retrieval_prov.append({"doc_id": ref["doc_id"], "label": ref["label"],
                                       "fallback": True, "passages": []})

    # External-intelligence grounding: fetched once per run and snapshotted in
    # resolution["connector_context"] (see create_run). Opted-in sections just
    # read it here — no per-section vendor calls. Empty unless a connector was
    # enabled AND some section opted in, so a document-only run is unchanged.
    if prompt_payload.get("uses_external_context"):
        for docs in (resolution.get("connector_context") or {}).values():
            grounding += docs

    # Section interlinking (FR-D08): a dependent section is grounded on the
    # generated OUTPUT of the sections it depends on — e.g. an executive summary
    # that consumes every other section. The dependency gate in _claim_next
    # guarantees those sections are already terminal; only ones that completed
    # with content contribute (a failed/skipped dependency is silently absent).
    dep_codes = getattr(job, "depends_on", None) or []
    if dep_codes:
        with SessionLocal() as db:
            rows = {r.section_code: r for r in db.scalars(select(SectionJob).where(
                SectionJob.run_id == run.id, SectionJob.kind == "initial",
                SectionJob.section_code.in_(dep_codes),
                SectionJob.status == "complete")).all()}
        for code in dep_codes:  # preserve declared dependency order
            row = rows.get(code)
            if row and (row.content or "").strip():
                grounding.append({
                    "doctype_code": "section_output",
                    "label": f"Section output · {row.name or code}",
                    "text": (row.content or "")[:_DEP_CONTENT_CAP]})

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
        # retrieval provenance (empty unless RAG is on) — surfaced in the trace;
        # ignored by the genai payload models (extra fields).
        "retrieval": retrieval_prov,
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
        # per-agent token log line for every agent call (FR observability)
        log.info("agent-tokens run=%s section=%s agent=%s model=%s in=%d out=%d",
                 run.id, job.section_code, agent, resp.get("model", "") or "unknown",
                 tokens_in, tokens_out)
        trace.append({"agent": agent, "model": resp.get("model", ""),
                      "tokens_in": tokens_in, "tokens_out": tokens_out, **extra})

    # 0 — RETRIEVAL (RAG): record which passages grounded this section so the
    # trace/audit shows exactly what the pipeline read (answers "why did it use
    # that passage?"). No token cost — retrieval is embedding + cosine.
    provenance = base.get("retrieval") or []
    if provenance:
        record("retrieval", {"usage": {}},
               docs=len(provenance),
               passages=sum(len(p.get("passages") or []) for p in provenance
                            if not p.get("fallback")),
               fallbacks=sum(1 for p in provenance if p.get("fallback")),
               retrieval=provenance)

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

    # 4 — CONSISTENCY CHECK AGENT (facts + cross-section figures). Runs here only
    # in per_section scope; in post_generation scope it is deferred to the
    # memo-level reconcile phase (see _run_reconcile), which sees every section.
    consistency_scope = pipeline_settings.get("consistency_scope", "post_generation")
    if pipeline_settings.get("agents_consistency_enabled", True) \
            and consistency_scope == "per_section":
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


def _revise_section_for_consistency(run: Run, job_id: str, verdict: dict,
                                    revision_limit: int, agent_rules: dict) -> bool:
    """Re-draft ONE section flagged by the reconcile agent, feeding its guidance
    in as summarisation feedback. Bounded by agent_revision_limit (0 disables
    re-drafting). Returns True if the content actually changed."""
    if revision_limit < 1:
        # cannot re-draft — record the unresolved inconsistency for the trailer
        with SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            checks = dict(job.checks or {})
            checks["consistency"] = {"passed": False, "scope": "post_generation",
                                     "inconsistencies": verdict.get("issues", []),
                                     "notes": (verdict.get("guidance") or "")[:300],
                                     "revisions": 0}
            job.checks = checks
            db.commit()
        return False

    with SessionLocal() as db:
        job = db.get(SectionJob, job_id)
        base = _section_payload(run, job)
        facts = job.facts or []
        trace = list(job.agent_trace or [])
        prev_content = job.content or ""
        tokens_in, tokens_out = job.tokens_in, job.tokens_out

    gen_payload = {**base, "extracted_facts": facts,
                   "agent_rules": agent_rules.get("summarisation"),
                   "feedback": {"inconsistencies": verdict.get("issues", []),
                                "guidance": verdict.get("guidance", "")}}
    generated = resolver.genai_generate(gen_payload)
    content = generated.get("content", "")
    usage = generated.get("usage") or {}
    ti, to = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    log.info("agent-tokens run=%s section=%s agent=%s model=%s in=%d out=%d",
             run.id, job.section_code, "summarisation:reconcile",
             generated.get("model", "") or "unknown", ti, to)
    trace.append({"agent": "summarisation:reconcile", "model": generated.get("model", ""),
                  "tokens_in": ti, "tokens_out": to, "trigger": "reconcile",
                  "issues": verdict.get("issues", [])})
    changed = content.strip() != prev_content.strip()

    with SessionLocal() as db:
        job = db.get(SectionJob, job_id)
        if changed:
            job.content = content
            job.untraceable = generated.get("untraceable_numbers", job.untraceable)
        job.agent_trace = trace
        job.tokens_in = tokens_in + ti
        job.tokens_out = tokens_out + to
        checks = dict(job.checks or {})
        checks["consistency"] = {
            "passed": True if changed else False,
            "scope": "post_generation",
            "inconsistencies": [] if changed else verdict.get("issues", []),
            "notes": ("re-drafted to resolve cross-section inconsistencies"
                      if changed else (verdict.get("guidance") or "")[:300]),
            "revisions": 1 if changed else 0}
        job.checks = checks
        db.commit()
    return changed


def _run_reconcile(run_id: str, job_id: str) -> None:
    """Memo-level cross-section consistency (consistency_scope=post_generation):
    reconcile every completed section together, then re-draft ONLY the sections
    the agent flags (bounded by agent_revision_limit). Fail-open end-to-end — any
    error leaves the drafts untouched and lets the run finalise."""
    try:
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            complete = list(db.scalars(select(SectionJob).where(
                SectionJob.run_id == run_id, SectionJob.kind == "initial",
                SectionJob.status == "complete").order_by(SectionJob.order_no)).all())
            sec_snap = [{"job_id": s.id, "section_code": s.section_code, "name": s.name,
                         "content": s.content or "", "figures": _figures_from_facts(s.facts)[:16]}
                        for s in complete]

        pipeline_settings = run.resolution.get("settings") or {}
        revision_limit = int(pipeline_settings.get("agent_revision_limit", 1))
        agent_rules = {role: (entry or {}).get("prompt_text")
                       for role, entry in (run.resolution.get("agent_rules") or {}).items()}

        verdict = resolver.genai_reconcile({
            "sections": [{"section_code": s["section_code"], "name": s["name"],
                          "content": s["content"], "figures": s["figures"]} for s in sec_snap],
            "agent_rules": agent_rules.get("consistency")})
        flagged = {v["section_code"]: v for v in (verdict.get("sections") or [])
                   if not v.get("consistent", True) and (v.get("guidance") or v.get("issues"))}

        revised: list[str] = []
        by_code = {s["section_code"]: s for s in sec_snap}
        for code, v in flagged.items():
            snap = by_code.get(code)
            if snap and _revise_section_for_consistency(run, snap["job_id"], v,
                                                        revision_limit, agent_rules):
                revised.append(code)

        # record a clean consistency verdict on the sections that were NOT flagged
        # so the audit trail / gap trailer shows the check ran for every section
        with SessionLocal() as db:
            for snap in sec_snap:
                if snap["section_code"] in flagged:
                    continue
                job = db.get(SectionJob, snap["job_id"])
                checks = dict(job.checks or {})
                checks["consistency"] = {"passed": True, "scope": "post_generation",
                                         "inconsistencies": [], "revisions": 0,
                                         "notes": "no cross-section inconsistency"}
                job.checks = checks
            db.commit()

        rusage = verdict.get("usage") or {}
        with SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.status = "complete"
            job.error = None
            job.tokens_in = int(rusage.get("input_tokens", 0))
            job.tokens_out = int(rusage.get("output_tokens", 0))
            job.agent_trace = [{"agent": "reconcile", "model": verdict.get("model", ""),
                                "tokens_in": job.tokens_in, "tokens_out": job.tokens_out,
                                "flagged": sorted(flagged.keys()), "revised": revised}]
            job.checks = {"reconcile": {"flagged": sorted(flagged.keys()), "revised": revised,
                                        "notes": verdict.get("notes", "")}}
            db.commit()
        audit.emit(settings, action="run.reconciled", entity_type="run", entity_id=run_id,
                   case_id=run.case_id, run_id=run_id,
                   detail={"flagged": sorted(flagged.keys()), "revised": revised,
                           "sections_seen": len(sec_snap)})
    except Exception as exc:
        log.exception("reconcile phase failed for run %s", run_id)
        with SessionLocal() as db:
            job = db.get(SectionJob, job_id)
            job.status = "failed"
            job.error = str(exc)[:1000]
            db.commit()

    _after_section(job_id)


def process_job(job_id: str) -> None:
    with SessionLocal() as db:
        job = db.get(SectionJob, job_id)
        run = db.get(Run, job.run_id)
    set_correlation_id(run.correlation_id)

    # memo-level cross-section consistency phase (consistency_scope=post_generation)
    if job.kind == "reconcile":
        _run_reconcile(run.id, job_id)
        return

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
                                          "tokens_in": job.tokens_in,
                                          "tokens_out": job.tokens_out,
                                          # per-agent token breakdown (immutable record)
                                          "token_usage": [
                                              {"agent": t["agent"], "model": t.get("model", ""),
                                               "tokens_in": t.get("tokens_in", 0),
                                               "tokens_out": t.get("tokens_out", 0)}
                                              for t in job.agent_trace],
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

    if job.kind == "reconcile":
        # the memo-level phase is done (or failed) — settle/hand off the run
        _maybe_finalize(job.run_id)
        return

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

            # Memo-level consistency phase (consistency_scope=post_generation):
            # once every section is drafted, run one cross-section reconcile pass
            # (which re-drafts only the sections it flags) BEFORE handing off the
            # CAM. Gated so it runs once; a failed reconcile still finalises.
            settings_snap = run.resolution.get("settings") or {}
            wants_reconcile = (
                settings_snap.get("consistency_scope", "post_generation") == "post_generation"
                and settings_snap.get("agents_consistency_enabled", True))
            if wants_reconcile and not run.cam_id and len(complete) >= 2:
                recon = db.scalar(select(SectionJob).where(
                    SectionJob.run_id == run_id, SectionJob.kind == "reconcile"))
                if recon is None:
                    db.add(SectionJob(run_id=run_id, section_code="_reconcile",
                                      name="Cross-section consistency", order_no=9998,
                                      kind="reconcile", status="queued"))
                    db.commit()
                    return  # defer CAM handoff until the reconcile phase completes
                if recon.status in ("queued", "running"):
                    return  # reconcile in flight — wait for it
                # reconcile terminal: re-read sections (revisions updated content)
                sections = list(db.scalars(select(SectionJob).where(
                    SectionJob.run_id == run_id, SectionJob.kind == "initial")).all())
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
            # runtime concurrency gate: workers indexed at/above the live active
            # concurrency idle this tick, so the admin setting scales the pool up
            # and down without a restart. Worker 0 is always active (active >= 1).
            if worker_no >= await asyncio.to_thread(_active_concurrency):
                worked = False
            else:
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
