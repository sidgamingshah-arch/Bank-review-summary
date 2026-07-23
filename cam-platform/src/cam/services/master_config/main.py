"""master-config service — the four+one business-administered masters
(BRD §6.1): prompts, templates, document types, industry taxonomy, KPI sets.
All changes are maker-checker controlled; only published versions resolve at
runtime; every lifecycle step is audited (FR-F03).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import Depends, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from cam.common import audit
from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.db import iso, make_engine, make_session_factory, utcnow
from cam.common.errors import ApiError
from cam.common.http import gateway_client, gateway_headers, raise_for_error
from cam.common.placeholders import resolve_placeholders
from cam.common.security import Principal, make_auth_dependencies

from . import engine as eng
from .csv_io import parse_kpi_csv, render_kpi_csv
from .models import DEFAULT_SETTINGS, MTYPES, MasterItem, MasterVersion, Setting
from .schemas import AGENT_RULE_KEYS, GLOBAL_PROMPT_KEY, validate_payload
from .xlsx_io import build_template_workbook, parse_workbook

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

settings = get_settings("master-config")
engine = make_engine(settings.resolved_db_url())
SessionLocal = make_session_factory(engine)
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM master-config service")


@app.on_event("startup")
def startup() -> None:
    from .models import Base
    Base.metadata.create_all(engine)


def _mtype(mtype_segment: str) -> str:
    if mtype_segment not in MTYPES:
        raise ApiError.not_found(f"master type '{mtype_segment}'")
    return MTYPES[mtype_segment]


def _catalogue(db) -> dict[str, set[str]]:
    return {"doctype_codes": eng.item_keys(db, "doctype"),
            "prompt_keys": eng.item_keys(db, "prompt"),
            "industry_codes": eng.item_keys(db, "industry")}


def _validate(db, mtype: str, key: str, payload: dict) -> dict:
    normalised, errors = validate_payload(mtype, key, payload, **_catalogue(db))
    if errors:
        raise ApiError.validation("payload validation failed", errors)
    return normalised


def _audit(action: str, mtype: str, key: str, version_no: int, principal: Principal,
           detail: dict | None = None) -> None:
    audit.emit(settings, action=action, entity_type=f"master:{mtype}", entity_id=key,
               principal=principal, detail={"version_no": version_no, **(detail or {})})


# ---------------------------------------------------------------- specific routes
# (registered before the generic /{mtype} routes so they never shadow-match)

def _llm_info() -> dict:
    """Read-only view of the deployment's LLM egress config, derived from this
    service's own (shared) Settings. The API key value is NEVER returned — only
    whether the configured env var is populated (NFR-06)."""
    import os
    return {
        "provider": settings.llm_provider,
        "model": settings.genai_model,
        "base_url": settings.genai_base_url or None,
        "max_tokens": settings.genai_max_tokens,
        "api_key_env": settings.genai_api_key_env,
        "api_key_configured": bool(os.environ.get(settings.genai_api_key_env)),
    }


@app.get("/api/masters/settings")
def get_settings_map(principal: Principal = Depends(require("masters:read"))):
    with SessionLocal() as db:
        stored = {s.key: s.value.get("value") for s in db.scalars(select(Setting)).all()}
    return {**DEFAULT_SETTINGS, **stored, "_llm": _llm_info()}


class SettingsPatch(BaseModel):
    tagging_confidence_threshold: float | None = None
    tagging_mode: Literal["ai_first", "keyword_first", "keyword_only"] | None = None
    agents_materiality_enabled: bool | None = None
    agents_consistency_enabled: bool | None = None
    agent_revision_limit: int | None = Field(default=None, ge=0, le=3)
    connectors_search_enabled: bool | None = None
    connectors_news_enabled: bool | None = None


@app.put("/api/masters/settings")
def put_settings(body: SettingsPatch, principal: Principal = Depends(require("masters:settings"))):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "tagging_confidence_threshold" in updates and not (
            0.0 <= updates["tagging_confidence_threshold"] <= 1.0):
        raise ApiError.validation("tagging_confidence_threshold must be within [0, 1]")
    with SessionLocal() as db:
        before = {s.key: s.value.get("value") for s in db.scalars(select(Setting)).all()}
        for key, value in updates.items():
            row = db.get(Setting, key)
            if row:
                row.value = {"value": value}
                row.updated_by = principal.username
            else:
                db.add(Setting(key=key, value={"value": value}, updated_by=principal.username))
        db.commit()
        audit.emit(settings, action="settings.updated", entity_type="settings", entity_id="global",
                   principal=principal, detail={"before": before, "after": updates})
        stored = {s.key: s.value.get("value") for s in db.scalars(select(Setting)).all()}
    return {**DEFAULT_SETTINGS, **stored, "_llm": _llm_info()}


@app.get("/api/masters/published/doctypes")
def published_doctypes(principal: Principal = Depends(require("masters:read"))):
    with SessionLocal() as db:
        out = []
        for item in db.scalars(select(MasterItem).where(MasterItem.mtype == "doctype")).all():
            v = eng.published_version(db, item)
            if v and v.payload.get("active", True):
                out.append(v.payload)
        return out


@app.get("/api/masters/resolve/template/{key}")
def resolve_template(key: str, principal: Principal = Depends(require("masters:read"))):
    """Runtime resolution for generation (FR-D01): template → section prompts →
    doc-type versions → global standing rules, published versions only."""
    with SessionLocal() as db:
        template_item = eng.require_item(db, "template", key)
        template_v = eng.published_version(db, template_item)
        if not template_v:
            raise ApiError(409, "not_published", f"template '{key}' has no published version")

        sections = []
        for s in sorted(template_v.payload["sections"], key=lambda x: x["order"]):
            prompt_item = eng.get_item(db, "prompt", s["section_code"])
            prompt_v = eng.published_version(db, prompt_item) if prompt_item else None
            if not prompt_v:
                raise ApiError(409, "not_published",
                               f"section '{s['section_code']}' has no published prompt")
            sections.append({**s, "prompt": {"key": s["section_code"],
                                             "version": prompt_v.version_no,
                                             "payload": prompt_v.payload}})

        global_item = eng.get_item(db, "prompt", GLOBAL_PROMPT_KEY)
        global_v = eng.published_version(db, global_item) if global_item else None

        # published agent-role standing rules (optional — agents run with
        # house defaults when a role has no published master entry)
        agent_rules: dict[str, dict] = {}
        for role, rule_key in AGENT_RULE_KEYS.items():
            rule_item = eng.get_item(db, "prompt", rule_key)
            rule_v = eng.published_version(db, rule_item) if rule_item else None
            if rule_v:
                agent_rules[role] = {"prompt_key": rule_key, "version": rule_v.version_no,
                                     "prompt_text": rule_v.payload["prompt_text"]}

        doctype_versions: dict[str, int] = {}
        referenced = set(template_v.payload.get("required_doc_types", []))
        for s in sections:
            referenced |= set(s["prompt"]["payload"].get("source_doc_types", []))
            if s.get("include_if_doctype"):
                referenced.add(s["include_if_doctype"])
        for code in sorted(referenced):
            dt_item = eng.get_item(db, "doctype", code)
            dt_v = eng.published_version(db, dt_item) if dt_item else None
            if not dt_v:
                raise ApiError(409, "not_published", f"document type '{code}' is not published")
            doctype_versions[code] = dt_v.version_no

        stored = {s.key: s.value.get("value") for s in db.scalars(select(Setting)).all()}
        return {
            "template_key": key, "template_version": template_v.version_no,
            "template": template_v.payload,
            "global_rules": ({"prompt_key": GLOBAL_PROMPT_KEY, "version": global_v.version_no,
                              "prompt_text": global_v.payload["prompt_text"]} if global_v else None),
            "agent_rules": agent_rules,
            "sections": sections,
            "doctype_master_versions": doctype_versions,
            "settings": {**DEFAULT_SETTINGS, **stored},
        }


@app.get("/api/masters/resolve/kpi-set/{industry_code}")
def resolve_kpi_set(industry_code: str, principal: Principal = Depends(require("masters:read"))):
    """Published KPI set + industry names for runtime injection (FR-A11)."""
    with SessionLocal() as db:
        industry_item = eng.get_item(db, "industry", industry_code)
        industry_v = eng.published_version(db, industry_item) if industry_item else None
        kpi_item = eng.get_item(db, "kpi_set", industry_code)
        kpi_v = eng.published_version(db, kpi_item) if kpi_item else None
        return {"industry": industry_v.payload if industry_v else None,
                "kpi_set_version": kpi_v.version_no if kpi_v else None,
                "kpis": kpi_v.payload["kpis"] if kpi_v else []}


@app.post("/api/masters/kpi-sets/bulk")
async def kpi_bulk_upload(file: UploadFile,
                          principal: Principal = Depends(require("masters:draft"))):
    grouped, errors = parse_kpi_csv(await file.read())
    created, updated = [], []
    with SessionLocal() as db:
        for industry_code, kpis in grouped.items():
            payload = {"industry_code": industry_code, "kpis": kpis}
            normalised, verrors = validate_payload("kpi_set", industry_code, payload,
                                                   **_catalogue(db))
            if verrors:
                errors.append({"row": 0, "message": f"{industry_code}: " + "; ".join(verrors)})
                continue
            item = eng.get_item(db, "kpi_set", industry_code)
            if item:
                v = eng.add_version(db, item, normalised, "bulk CSV upload", principal.username)
                updated.append({"industry_code": industry_code, "version_no": v.version_no})
            else:
                _, v = eng.create_item(db, "kpi_set", industry_code, normalised,
                                       "bulk CSV upload", principal.username)
                created.append({"industry_code": industry_code, "version_no": v.version_no})
            _audit("master.version_created", "kpi_set", industry_code, v.version_no, principal,
                   {"source": "bulk_csv"})
        db.commit()
    return {"created": created, "updated": updated, "errors": errors}


@app.get("/api/masters/kpi-sets/export.csv")
def kpi_export(principal: Principal = Depends(require("masters:read"))):
    with SessionLocal() as db:
        payloads = []
        for item in db.scalars(select(MasterItem).where(MasterItem.mtype == "kpi_set")).all():
            v = eng.published_version(db, item)
            if v:
                payloads.append(v.payload)
    return Response(render_kpi_csv(payloads), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=kpi_sets.csv"})


class SandboxRequest(BaseModel):
    sample_docs: list[dict] = []
    placeholders: dict = {}


def call_genai_generate(payload: dict) -> dict:
    """Monkeypatchable in tests; sandbox runs go through the gateway like any
    other GenAI call (NFR-10)."""
    with gateway_client(settings) as client:
        resp = client.post("/api/genai/generate", json=payload, headers=gateway_headers(settings))
        raise_for_error(resp, "genai generate")
        return resp.json()


@app.post("/api/masters/prompts/{key}/sandbox-test")
def sandbox_test(key: str, body: SandboxRequest,
                 principal: Principal = Depends(require("masters:draft"))):
    """FR-A05: test-run the LATEST version (draft included) before publishing."""
    with SessionLocal() as db:
        item = eng.require_item(db, "prompt", key)
        version = max(eng.versions_of(db, item), key=lambda v: v.version_no)
        global_item = eng.get_item(db, "prompt", GLOBAL_PROMPT_KEY)
        global_v = eng.published_version(db, global_item) if global_item else None

    defaults = {"borrower_name": "Sample Borrower Ltd", "case_type": "corporate",
                "relationship": "etb", "industry_name": "Sample Industry",
                "industry_kpis": "(sandbox: no KPI set injected)",
                "today": iso(utcnow()) or ""}
    resolved, _ = resolve_placeholders(version.payload["prompt_text"],
                                       {**defaults, **body.placeholders})
    result = call_genai_generate({
        "mode": "section",
        "layers": {"global_rules": global_v.payload["prompt_text"] if global_v else None,
                   "template_instructions": None, "section_prompt": resolved},
        "placeholders": defaults | body.placeholders,
        "grounding_docs": [{"doctype_code": d.get("doctype_code", "sample"),
                            "label": d.get("doctype_code", "sample"),
                            "text": d.get("text", "")} for d in body.sample_docs],
        "preferences": None, "fixed_format": False, "length_guidance": None,
        "model_overrides": version.payload.get("model_overrides"),
    })
    _audit("master.sandbox_tested", "prompt", key, version.version_no, principal)
    return {"content": result.get("content", ""), "model": result.get("model", ""),
            "usage": result.get("usage", {}), "version_tested": version.version_no}


# ------------------------------------------------------- configuration portability

class BundleEntry(BaseModel):
    mtype: str  # internal type name: prompt|template|doctype|industry|kpi_set
    key: str
    payload: dict


class BundleImport(BaseModel):
    masters: list[BundleEntry]


# import order honours referential validation: doc types and taxonomy first,
# then prompts (validated against doc types), KPI sets (against taxonomy),
# templates last (against prompts + doc types)
_IMPORT_ORDER = {"doctype": 0, "industry": 1, "prompt": 2, "kpi_set": 3, "template": 4}


@app.get("/api/masters/export-bundle")
def export_bundle(principal: Principal = Depends(require("masters:read"))):
    """Environment portability (deploy-time configuration swap): every
    currently-published master as one JSON bundle, plus settings."""
    if not (principal.can("masters:settings") or principal.can("audit:export")
            or principal.is_service):
        raise ApiError.forbidden("bundle export is for business-admin/auditor roles")
    with SessionLocal() as db:
        masters = []
        for item in db.scalars(select(MasterItem).order_by(MasterItem.mtype,
                                                           MasterItem.key)).all():
            version = eng.published_version(db, item)
            if version:
                masters.append({"mtype": item.mtype, "key": item.key,
                                "version": version.version_no, "payload": version.payload})
        stored = {s.key: s.value.get("value") for s in db.scalars(select(Setting)).all()}
    audit.emit(settings, action="master.bundle_exported", entity_type="masters",
               entity_id="bundle", principal=principal, detail={"count": len(masters)})
    return {"platform": "cam-platform", "bundle_version": 1,
            "masters": masters, "settings": {**DEFAULT_SETTINGS, **stored}}


def _import_entries(db, entries: list[dict], principal: Principal,
                    change_note: str) -> dict:
    """Land a list of {mtype,key,payload} dicts as DRAFTS, in dependency order.
    Shared by the JSON bundle import and the Excel bulk upload. Entries that
    match the current published payload are skipped (idempotent, no 409).
    Caller commits."""
    created, updated, unchanged, errors = [], [], [], []
    ordered = sorted(entries, key=lambda e: _IMPORT_ORDER.get(e["mtype"], 9))
    for entry in ordered:
        mtype, key, payload = entry["mtype"], entry["key"], entry["payload"]
        ref = f"{mtype}:{key}"
        if mtype not in _IMPORT_ORDER:
            errors.append({"entry": ref, "message": "unknown master type"})
            continue
        normalised, verrors = validate_payload(mtype, key, payload, **_catalogue(db))
        if verrors:
            errors.append({"entry": ref, "message": "; ".join(verrors)})
            continue
        item = eng.get_item(db, mtype, key)
        if item:
            published = eng.published_version(db, item)
            if published and published.payload == normalised:
                unchanged.append(ref)
                continue
            version = eng.add_version(db, item, normalised, change_note, principal.username)
            updated.append({"entry": ref, "version_no": version.version_no})
        else:
            _, version = eng.create_item(db, mtype, key, normalised, change_note,
                                         principal.username)
            created.append({"entry": ref, "version_no": version.version_no})
        db.flush()  # catalogue grows as entries import (ordering above)
    return {"created": created, "updated": updated, "unchanged": unchanged, "errors": errors}


@app.post("/api/masters/import-bundle")
def import_bundle(body: BundleImport,
                  principal: Principal = Depends(require("masters:draft"))):
    """Import a bundle as DRAFTS — maker-checker still governs publication.
    Entries identical to the current published payload are skipped."""
    with SessionLocal() as db:
        result = _import_entries(db, [e.model_dump() for e in body.masters],
                                 principal, "bundle import")
        db.commit()
    audit.emit(settings, action="master.bundle_imported", entity_type="masters",
               entity_id="bundle", principal=principal,
               detail={k: len(result[k]) for k in ("created", "updated", "unchanged", "errors")})
    return {**result, "note": "imported versions are drafts; submit + approve to publish"}


@app.get("/api/masters/bulk-template")
def bulk_template(principal: Principal = Depends(require("masters:read"))):
    """Download the Excel bulk-upload template (one sheet per master type,
    worked example rows, README)."""
    return Response(build_template_workbook(), media_type=XLSX_MEDIA,
                    headers={"Content-Disposition":
                             "attachment; filename=cam-masters-template.xlsx"})


@app.post("/api/masters/bulk-upload")
async def bulk_upload(file: UploadFile,
                      principal: Principal = Depends(require("masters:draft"))):
    """Bulk-create/update masters from a filled-in template workbook. Every
    entry lands as a DRAFT (maker-checker unchanged); returns a per-entry report."""
    entries, parse_errors = parse_workbook(await file.read())
    with SessionLocal() as db:
        result = _import_entries(db, entries, principal, "bulk workbook upload")
        db.commit()
    result["errors"] = parse_errors + result["errors"]
    audit.emit(settings, action="master.bulk_uploaded", entity_type="masters",
               entity_id="bulk", principal=principal,
               detail={k: len(result[k]) for k in ("created", "updated", "unchanged", "errors")})
    return {**result, "note": "imported versions are drafts; submit + approve to publish"}


# ---------------------------------------------------------------- generic master routes

class ItemCreate(BaseModel):
    key: str
    payload: dict
    change_note: str = ""


class VersionCreate(BaseModel):
    payload: dict
    change_note: str = ""
    effective_from: datetime | None = None


class RejectBody(BaseModel):
    reason: str


@app.get("/api/masters/{mtype_segment}")
def list_items(mtype_segment: str, principal: Principal = Depends(require("masters:read"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        out = []
        for item in db.scalars(select(MasterItem).where(MasterItem.mtype == mtype)
                               .order_by(MasterItem.key)).all():
            versions = eng.versions_of(db, item)
            published = eng.published_version(db, item)
            latest = max(versions, key=lambda v: v.version_no) if versions else None
            out.append({"key": item.key, "item_id": item.id,
                        "latest_version": latest.version_no if latest else None,
                        "latest_status": latest.status if latest else None,
                        "published_version": published.version_no if published else None,
                        "updated_at": iso(max(v.created_at for v in versions)) if versions else None})
        return out


@app.post("/api/masters/{mtype_segment}", status_code=201)
def create_item(mtype_segment: str, body: ItemCreate,
                principal: Principal = Depends(require("masters:draft"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        normalised = _validate(db, mtype, body.key, body.payload)
        item, version = eng.create_item(db, mtype, body.key, normalised,
                                        body.change_note, principal.username)
        db.commit()
        _audit("master.created", mtype, body.key, 1, principal,
               {"change_note": body.change_note})
        return {"key": item.key, "item_id": item.id, "versions": [version.meta()],
                "published_version": None}


@app.get("/api/masters/{mtype_segment}/{key}")
def get_item(mtype_segment: str, key: str,
             principal: Principal = Depends(require("masters:read"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        published = eng.published_version(db, item)
        return {"key": item.key, "item_id": item.id,
                "versions": [v.meta() for v in eng.versions_of(db, item)],
                "published_version": published.version_no if published else None}


@app.get("/api/masters/{mtype_segment}/{key}/versions/{version_no}")
def get_version(mtype_segment: str, key: str, version_no: int,
                principal: Principal = Depends(require("masters:read"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        version = eng.get_version(db, item, version_no)
        if version.status != "published" and not principal.can("masters:draft") \
                and not principal.is_service:
            raise ApiError.forbidden("only published versions are visible to this role")
        return version.to_dict()


@app.post("/api/masters/{mtype_segment}/{key}/versions", status_code=201)
def create_version(mtype_segment: str, key: str, body: VersionCreate,
                   principal: Principal = Depends(require("masters:draft"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        normalised = _validate(db, mtype, key, body.payload)
        version = eng.add_version(db, item, normalised, body.change_note,
                                  principal.username, body.effective_from)
        db.commit()
        _audit("master.version_created", mtype, key, version.version_no, principal,
               {"change_note": body.change_note})
        return version.to_dict()


@app.post("/api/masters/{mtype_segment}/{key}/versions/{version_no}/submit")
def submit_version(mtype_segment: str, key: str, version_no: int,
                   principal: Principal = Depends(require("masters:submit"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        version = eng.submit(db, eng.get_version(db, item, version_no), principal.username)
        db.commit()
        _audit("master.submitted", mtype, key, version_no, principal)
        return version.to_dict()


@app.post("/api/masters/{mtype_segment}/{key}/versions/{version_no}/approve")
def approve_version(mtype_segment: str, key: str, version_no: int,
                    principal: Principal = Depends(require("masters:approve"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        previous = eng.published_version(db, item)
        version = eng.approve(db, item, eng.get_version(db, item, version_no),
                              principal.username)
        db.commit()
        _audit("master.approved", mtype, key, version_no, principal,
               {"previous_published": previous.version_no if previous else None,
                "maker": version.created_by})
        return version.to_dict()


@app.post("/api/masters/{mtype_segment}/{key}/versions/{version_no}/reject")
def reject_version(mtype_segment: str, key: str, version_no: int, body: RejectBody,
                   principal: Principal = Depends(require("masters:approve"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        version = eng.reject(db, eng.get_version(db, item, version_no),
                             principal.username, body.reason)
        db.commit()
        _audit("master.rejected", mtype, key, version_no, principal, {"reason": body.reason})
        return version.to_dict()


@app.post("/api/masters/{mtype_segment}/{key}/versions/{version_no}/rollback", status_code=201)
def rollback_version(mtype_segment: str, key: str, version_no: int,
                     principal: Principal = Depends(require("masters:draft"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        source = eng.get_version(db, item, version_no)
        version = eng.rollback(db, item, source, principal.username)
        db.commit()
        _audit("master.rolled_back", mtype, key, version.version_no, principal,
               {"source_version": version_no})
        return version.to_dict()


@app.get("/api/masters/{mtype_segment}/{key}/diff")
def diff(mtype_segment: str, key: str,
         frm: int = Query(alias="from"), to: int = Query(),
         principal: Principal = Depends(require("masters:read"))):
    mtype = _mtype(mtype_segment)
    with SessionLocal() as db:
        item = eng.require_item(db, mtype, key)
        a = eng.get_version(db, item, frm)
        b = eng.get_version(db, item, to)
        return {"diff": eng.diff_versions(a, b)}
