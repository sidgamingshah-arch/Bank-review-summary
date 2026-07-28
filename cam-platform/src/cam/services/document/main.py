"""document service — cases, VAF document intake, tags, completeness
(FR-C01..C07, contracts.md §3).

Binaries and text extracts live on disk (blob-store stand-in), never in the
DB (NFR-03). Cross-service calls (tagging classify, master-config resolve) go
through the gateway and are wrapped in small module-level functions so tests
can monkeypatch them.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from cam.common import audit
from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.db import make_engine, make_session_factory
from cam.common.errors import ApiError
from cam.common.http import gateway_client, gateway_headers, raise_for_error
from cam.common.rbac import is_own_scoped
from cam.common.security import Principal, make_auth_dependencies

from . import azure_search, embedding, retrieval, storage, vaf
from .models import Base, Case, Document, DocumentChunk, DocumentTag

settings = get_settings("document")
engine = make_engine(settings.resolved_db_url())
SessionLocal = make_session_factory(engine)
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM document service")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(engine)


def fetch_resolved_template(template_key: str) -> dict:
    """GET /api/masters/resolve/template/{key} via the gateway (monkeypatched
    in tests). Upstream errors (404/not_published) propagate to the caller."""
    with gateway_client(settings, timeout=15.0) as client:
        resp = client.get(f"/api/masters/resolve/template/{template_key}",
                          headers=gateway_headers(settings))
        raise_for_error(resp, "resolve template")
        return resp.json()


# ---------------------------------------------------------------- scoping

def _scoped_case(db: Session, case_id: str, principal: Principal) -> Case:
    """404 for unknown case; 403 when an own-scoped analyst hits someone
    else's case; reviewers/auditors/services see all."""
    case = db.get(Case, case_id)
    if not case:
        raise ApiError.not_found("case")
    if (not principal.is_service and is_own_scoped(principal.roles)
            and case.created_by != principal.username):
        raise ApiError.forbidden("analysts can only access their own cases")
    return case


def _scoped_document(db: Session, document_id: str, principal: Principal) -> Document:
    doc = db.get(Document, document_id)
    if not doc:
        raise ApiError.not_found("document")
    _scoped_case(db, doc.case_id, principal)
    return doc


# ------------------------------------------------------------------ cases

class CaseCreate(BaseModel):
    borrower_name: str
    segment: str
    relationship: str
    industry_code: str


@app.post("/api/cases", status_code=201)
def create_case(body: CaseCreate, principal: Principal = Depends(require("case:create"))):
    if not body.borrower_name.strip():
        raise ApiError.validation("borrower_name must not be empty")
    with SessionLocal() as db:
        case = Case(borrower_name=body.borrower_name.strip(), segment=body.segment,
                    relationship=body.relationship, industry_code=body.industry_code,
                    created_by=principal.username)
        db.add(case)
        db.commit()
        audit.emit(settings, action="case.created", entity_type="case", entity_id=case.id,
                   principal=principal, case_id=case.id,
                   detail={"borrower_name": case.borrower_name, "segment": case.segment,
                           "relationship": case.relationship,
                           "industry_code": case.industry_code})
        return case.to_dict()


@app.get("/api/cases")
def list_cases(principal: Principal = Depends(require("case:read"))):
    with SessionLocal() as db:
        q = select(Case).order_by(Case.created_at.desc())
        if not principal.is_service and is_own_scoped(principal.roles):
            q = q.where(Case.created_by == principal.username)
        return [c.to_dict() for c in db.scalars(q).all()]


class CaseStatusPatch(BaseModel):
    status: Literal["open", "generating", "drafted", "finalised"]


@app.patch("/api/cases/{case_id}/status")
def patch_case_status(case_id: str, body: CaseStatusPatch,
                      principal: Principal = Depends(require_service)):
    """Case lifecycle notifications from orchestration (generating/drafted)
    and output (finalised). Service-internal; advisory state for the UI."""
    with SessionLocal() as db:
        case = db.get(Case, case_id)
        if not case:
            raise ApiError.not_found("case")
        before = case.status
        case.status = body.status
        db.commit()
        audit.emit(settings, action="case.status_changed", entity_type="case",
                   entity_id=case.id, principal=principal, case_id=case.id,
                   detail={"before": before, "after": body.status})
        return case.to_dict()


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, principal: Principal = Depends(require("case:read"))):
    with SessionLocal() as db:
        return _scoped_case(db, case_id, principal).to_dict()


# -------------------------------------------------------------- documents

@app.post("/api/cases/{case_id}/documents", status_code=201)
def upload_document(case_id: str,
                    file: list[UploadFile] = File(...),
                    origin: str = Form("upload"),
                    period_label: str | None = Form(None),
                    principal: Principal = Depends(require("docs:manage"))):
    # FR-C02/NFR-07: strictly one file per request — the FE fans a
    # multi-select out into sequential single-file uploads.
    if len(file) != 1:
        raise ApiError.validation("exactly one file per request; "
                                  "multi-select must fan out to sequential uploads")
    if origin not in ("upload", "chat", "repository"):
        raise ApiError.validation("origin must be one of: upload, chat, repository")
    upload = file[0]
    content = upload.file.read()
    with SessionLocal() as db:
        case = _scoped_case(db, case_id, principal)
        doc = vaf.process_file(db, case=case, filename=upload.filename or "upload.bin",
                               content=content, content_type=upload.content_type,
                               origin=origin, period_label=period_label,
                               principal=principal, action="document.uploaded")
        return doc.to_dict()


class PullRequest(BaseModel):
    source: Literal["repository"]
    external_ref: str


@app.post("/api/cases/{case_id}/pull", status_code=201)
def pull_document(case_id: str, body: PullRequest,
                  principal: Principal = Depends(require("docs:manage"))):
    """Repository-pull stand-in (FR-C03): loads a fixture blob from
    ``<data_dir>/repository/<external_ref>`` through the SAME pipeline."""
    repo_dir = (Path(settings.data_dir) / "repository").resolve()
    fixture = (repo_dir / body.external_ref).resolve()
    if not str(fixture).startswith(str(repo_dir) + os.sep) or not fixture.is_file():
        raise ApiError.not_found("repository document")
    with SessionLocal() as db:
        case = _scoped_case(db, case_id, principal)
        doc = vaf.process_file(db, case=case, filename=fixture.name,
                               content=fixture.read_bytes(), content_type=None,
                               origin="repository", period_label=None,
                               principal=principal, action="document.pulled",
                               extra_detail={"external_ref": body.external_ref})
        return doc.to_dict()


@app.get("/api/cases/{case_id}/documents")
def list_documents(case_id: str, principal: Principal = Depends(require("case:read"))):
    with SessionLocal() as db:
        _scoped_case(db, case_id, principal)
        docs = db.scalars(select(Document).where(Document.case_id == case_id)
                          .order_by(Document.uploaded_at.asc())).all()
        return [d.to_dict() for d in docs]


@app.get("/api/documents/{document_id}")
def get_document(document_id: str, principal: Principal = Depends(require("case:read"))):
    with SessionLocal() as db:
        return _scoped_document(db, document_id, principal).to_dict()


@app.get("/api/documents/{document_id}/text")
def get_document_text(document_id: str,
                      principal: Principal = Depends(require("case:read"))):
    """Extracted text for grounding — service tokens (orchestration/output)
    or any user with access to the case."""
    with SessionLocal() as db:
        doc = _scoped_document(db, document_id, principal)
    if doc.status == "quarantined":
        raise ApiError.conflict("document is quarantined and unusable", code="quarantined")
    return {"text": storage.read_extract(doc.id)}


class RetrieveRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=100)
    query: str = ""
    top_k: int = Field(default=6, ge=1, le=50)
    mode: Literal["embedding", "keyword"] = "embedding"


@app.post("/api/documents/retrieve")
def retrieve(body: RetrieveRequest, principal: Principal = Depends(require("case:read"))):
    """Large-document retrieval (RAG): the top-K most relevant chunks per
    document for a query, so a section is grounded on relevant passages rather
    than the first MAX_DOC_CHARS of full text. ``mode`` is 'embedding' (semantic,
    needs a query vector) or 'keyword' (lexical, no embedding model). Service
    tokens (orchestration) or any user with access to the case. Fail-open: if the
    query cannot be embedded or a document has no matching chunks, that document
    returns no chunks and the caller falls back to full-text grounding."""
    qv = None
    if body.mode == "embedding":
        query_vecs = embedding.embed_texts([body.query])
        qv = query_vecs[0] if query_vecs else None
    results: list[dict] = []
    with SessionLocal() as db:
        for doc_id in body.doc_ids:
            doc = db.get(Document, doc_id)
            if not doc:
                results.append({"doc_id": doc_id, "chunks": [], "reason": "not_found"})
                continue
            _scoped_case(db, doc.case_id, principal)  # RBAC (raises 403 if unauthorised)
            if doc.status == "quarantined":
                results.append({"doc_id": doc_id, "chunks": [], "reason": "quarantined"})
                continue
            if body.mode == "embedding" and qv is None:
                results.append({"doc_id": doc_id, "chunks": [], "reason": "query_not_embedded"})
                continue
            if azure_search.enabled():
                hits = azure_search.search_one(doc_id, body.query, qv, body.top_k, body.mode)
            else:
                chunks = db.scalars(select(DocumentChunk)
                                    .where(DocumentChunk.document_id == doc_id)).all()
                if body.mode == "keyword":
                    hits = retrieval.rank_keyword(body.query, chunks, body.top_k)
                else:
                    hits = retrieval.rank(qv, chunks, body.top_k)
            empty_reason = "no_match" if body.mode == "keyword" else "not_embedded"
            results.append({"doc_id": doc_id, "chunks": hits,
                            "reason": "" if hits else empty_reason})
    return {"results": results, "query_embedded": qv is not None, "mode": body.mode}


@app.post("/api/documents/{document_id}/reindex")
def reindex_document(document_id: str,
                     principal: Principal = Depends(require("docs:manage"))):
    """(Re)chunk and index a document on demand — for documents uploaded before
    RAG was enabled, or to refresh after an embedding/config change. Uses the
    current retrieval mode (falls back to 'embedding' if retrieval is off).
    Idempotent (existing chunks are replaced)."""
    mode = vaf.fetch_rag_mode()
    if mode == "off":
        mode = "embedding"
    with SessionLocal() as db:
        doc = _scoped_document(db, document_id, principal)
        if doc.status != "ready":
            raise ApiError.conflict("document has no extractable text to index",
                                    code="no_text")
        text = storage.read_extract(doc.id)
        n = vaf.index_document_chunks(db, doc, text, mode)
        case_id = doc.case_id
    if n:
        audit.emit(settings, action="document.indexed", entity_type="document",
                   entity_id=document_id, principal=principal, case_id=case_id,
                   detail={"chunks": n, "mode": mode, "chars": len(text), "reindex": True})
    return {"document_id": document_id, "chunks": n, "mode": mode, "indexed": bool(n)}


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str,
                    principal: Principal = Depends(require("docs:manage"))):
    with SessionLocal() as db:
        doc = _scoped_document(db, document_id, principal)
        vaf.remove_stored_files(doc)
        case_id, detail = doc.case_id, {"filename": doc.filename, "sha256": doc.sha256}
        db.delete(doc)  # cascades to tags (and local chunks)
        db.commit()
    if azure_search.enabled():
        azure_search.delete_document(document_id)  # fail-open
    audit.emit(settings, action="document.deleted", entity_type="document",
               entity_id=document_id, principal=principal, case_id=case_id, detail=detail)


# ------------------------------------------------------------------- tags

class TagCreate(BaseModel):
    doctype_code: str
    period_label: str | None = None
    seq_order: int | None = None
    page_range: str | None = None


class TagPatch(BaseModel):
    doctype_code: str | None = None
    period_label: str | None = None
    seq_order: int | None = None
    confirmed: bool | None = None


@app.post("/api/documents/{document_id}/tags", status_code=201)
def add_tag(document_id: str, body: TagCreate,
            principal: Principal = Depends(require("docs:manage"))):
    if not body.doctype_code.strip():
        raise ApiError.validation("doctype_code must not be empty")
    with SessionLocal() as db:
        doc = _scoped_document(db, document_id, principal)
        tag = DocumentTag(document_id=doc.id, doctype_code=body.doctype_code.strip(),
                          confidence=None, source="user", needs_review=False,
                          period_label=body.period_label, seq_order=body.seq_order,
                          page_range=body.page_range)
        db.add(tag)
        db.commit()
        audit.emit(settings, action="tag.added", entity_type="tag", entity_id=tag.id,
                   principal=principal, case_id=doc.case_id,
                   detail={"document_id": doc.id, "before": None, "after": tag.to_dict()})
        return tag.to_dict()


def _scoped_tag(db: Session, doc: Document, tag_id: str) -> DocumentTag:
    tag = db.get(DocumentTag, tag_id)
    if not tag or tag.document_id != doc.id:
        raise ApiError.not_found("tag")
    return tag


@app.patch("/api/documents/{document_id}/tags/{tag_id}")
def patch_tag(document_id: str, tag_id: str, body: TagPatch,
              principal: Principal = Depends(require("docs:manage"))):
    with SessionLocal() as db:
        doc = _scoped_document(db, document_id, principal)
        tag = _scoped_tag(db, doc, tag_id)
        before = tag.to_dict()
        provided = body.model_fields_set
        if body.doctype_code is not None:
            if not body.doctype_code.strip():
                raise ApiError.validation("doctype_code must not be empty")
            tag.doctype_code = body.doctype_code.strip()
        if "period_label" in provided:
            tag.period_label = body.period_label
        if "seq_order" in provided:
            tag.seq_order = body.seq_order
        if body.confirmed:
            tag.needs_review = False  # human confirmed; source stays as-is
        db.commit()
        audit.emit(settings, action="tag.changed", entity_type="tag", entity_id=tag.id,
                   principal=principal, case_id=doc.case_id,
                   detail={"document_id": doc.id, "before": before, "after": tag.to_dict()})
        return tag.to_dict()


@app.delete("/api/documents/{document_id}/tags/{tag_id}", status_code=204)
def delete_tag(document_id: str, tag_id: str,
               principal: Principal = Depends(require("docs:manage"))):
    with SessionLocal() as db:
        doc = _scoped_document(db, document_id, principal)
        tag = _scoped_tag(db, doc, tag_id)
        before = tag.to_dict()
        db.delete(tag)
        db.commit()
        audit.emit(settings, action="tag.removed", entity_type="tag", entity_id=tag_id,
                   principal=principal, case_id=doc.case_id,
                   detail={"document_id": doc.id, "before": before, "after": None})


# ----------------------------------------------------------- completeness

@app.get("/api/cases/{case_id}/completeness")
def completeness(case_id: str, template_key: str,
                 principal: Principal = Depends(require("case:read"))):
    with SessionLocal() as db:
        _scoped_case(db, case_id, principal)
        tagged = db.scalars(
            select(DocumentTag.doctype_code)
            .join(Document, DocumentTag.document_id == Document.id)
            .where(Document.case_id == case_id, Document.status != "quarantined")
            .distinct()
        ).all()
    resolved = fetch_resolved_template(template_key)
    required = list((resolved.get("template") or {}).get("required_doc_types") or [])
    present = set(tagged)
    missing = [code for code in required if code not in present]
    # v1: gaps warn but never block — the run carries a data-gap trailer
    # instead (FR-D05), so can_proceed is always true.
    return {"required": required, "present": sorted(present), "missing": missing,
            "can_proceed": True}
