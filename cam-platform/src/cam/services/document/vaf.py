"""VAF intake pipeline (FR-C02, FR-C03, FR-C07, NFR-07) — STRICTLY one file
per request.

Steps (synchronous): validate (extension/size/empty) → AV scan → sha256 +
duplicate check → store blob → extract text → auto-tag via tagging service.
Any validation/AV failure persists the Document as ``quarantined`` (the user
must see the reason) but the file content is never stored.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from cam.common import audit
from cam.common.config import get_settings
from cam.common.db import new_id
from cam.common.http import gateway_client, gateway_headers
from cam.common.security import Principal

from . import azure_search, chunking, embedding, storage
from .extraction import extract_text
from .models import Case, Document, DocumentChunk, DocumentTag

settings = get_settings("document")

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt"}

# Standard EICAR anti-virus test signature (industry-wide harmless test
# string). This is a stub: production swaps it for the bank's VAF/ICAP
# anti-virus integration — the contract (scan verdict → quarantine with a
# human-readable reason) stays identical.
EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def classify_document(filename: str, text: str) -> dict | None:
    """POST /api/tagging/classify via the gateway. Fail-open: returns None on
    any error so intake never fails because tagging is down (monkeypatched in
    tests)."""
    try:
        with gateway_client(settings, timeout=30.0) as client:
            resp = client.post("/api/tagging/classify",
                               json={"filename": filename, "text": text},
                               headers=gateway_headers(settings))
            if resp.status_code >= 400:
                return None
            return resp.json()
    except Exception:
        return None


def resolve_rag_mode(s: dict) -> str:
    """Normalise the retrieval mode from a master-settings dict. rag_mode
    (off|keyword|embedding) wins; the legacy boolean rag_enabled maps True ->
    'embedding' for back-compat."""
    mode = s.get("rag_mode")
    if mode in ("off", "keyword", "embedding"):
        return mode
    return "embedding" if s.get("rag_enabled") else "off"


def fetch_rag_mode() -> str:
    """Current retrieval mode from master settings (off|keyword|embedding).
    Fail-open to the deployment default so intake never breaks if master-config
    is unreachable (monkeypatched in tests)."""
    env_default = {"rag_mode": settings.rag_mode, "rag_enabled": settings.rag_enabled}
    try:
        with gateway_client(settings, timeout=10.0) as client:
            resp = client.get("/api/masters/settings", headers=gateway_headers(settings))
            if resp.status_code >= 400:
                return resolve_rag_mode(env_default)
            return resolve_rag_mode(resp.json())
    except Exception:
        return resolve_rag_mode(env_default)


def index_document_chunks(db: Session, doc: Document, text: str, mode: str = "embedding") -> int:
    """Chunk a document and (re)store it for retrieval, returning the chunk count.
    mode 'embedding' stores vectors (via the embedding egress); mode 'keyword'
    stores chunk text only (no embedding model needed). Idempotent — existing
    chunks are replaced. Fail-open: on any error (or when embedding is
    unavailable) nothing is stored and 0 is returned, so intake proceeds and
    retrieval later falls back to full-text grounding."""
    try:
        chunks = chunking.chunk_text(text, size=settings.rag_chunk_size,
                                     overlap=settings.rag_chunk_overlap)
        if len(chunks) > settings.rag_max_chunks:
            logging.getLogger("cam.document").warning(
                "document %s produced %d chunks; capping at %d (tail not indexed)",
                doc.id, len(chunks), settings.rag_max_chunks)
            chunks = chunks[: settings.rag_max_chunks]
        if not chunks:
            return 0
        vectors = None
        if mode == "embedding":
            vectors = embedding.embed_texts([c["text"] for c in chunks])
            if not vectors or len(vectors) != len(chunks):
                return 0  # embedding unavailable -> store nothing (fail-open)
        # Managed Azure AI Search index: push chunks there instead of the local
        # DocumentChunk table (keeps a single source of truth per backend).
        if azure_search.enabled():
            payload = [{"ordinal": c["ordinal"], "text": c["text"],
                        "char_start": c["char_start"], "char_end": c["char_end"],
                        **({"vector": vectors[i]} if vectors else {})}
                       for i, c in enumerate(chunks)]
            return azure_search.upsert_chunks(doc.id, doc.case_id, payload)
        db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
        rows = []
        for i, c in enumerate(chunks):
            vec = vectors[i] if vectors else None
            rows.append(DocumentChunk(
                document_id=doc.id, chunk_index=c["ordinal"],
                char_start=c["char_start"], char_end=c["char_end"], text=c["text"],
                embedding=vec, dim=len(vec) if isinstance(vec, list) else 0))
        db.add_all(rows)
        db.commit()
        return len(rows)
    except Exception:
        logging.getLogger("cam.document").exception(
            "chunk/index failed for document %s; proceeding without retrieval index", doc.id)
        db.rollback()
        return 0


def remove_stored_files(doc: Document) -> None:
    """Delete a document's blob and extract from the store (used by DELETE)."""
    storage.delete_doc(doc.id, Path(doc.filename).suffix.lower())


def _validation_failure(ext: str, content: bytes) -> str | None:
    if ext not in ALLOWED_EXTENSIONS:
        allowed = " ".join(sorted(ALLOWED_EXTENSIONS))
        return f"file type '{ext or '(none)'}' not allowed; accepted: {allowed}"
    if not content:
        return "empty file"
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        return f"file exceeds {settings.max_upload_mb} MB limit"
    return None


def _av_scan_failure(content: bytes) -> str | None:
    """AV scan stub — see EICAR_SIGNATURE note above."""
    if EICAR_SIGNATURE in content:
        return "malware signature detected (EICAR test signature)"
    return None


def process_file(db: Session, *, case: Case, filename: str, content: bytes,
                 content_type: str | None, origin: str, period_label: str | None,
                 principal: Principal, action: str,
                 extra_detail: dict | None = None) -> Document:
    """Run the full intake pipeline for exactly one file and return the
    persisted Document. ``action`` is the success audit action
    (``document.uploaded`` | ``document.pulled``)."""
    ext = Path(filename).suffix.lower()
    ctype = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    sha256 = hashlib.sha256(content).hexdigest()

    reason = _validation_failure(ext, content) or _av_scan_failure(content)
    if reason:
        # Persist the record so the analyst sees why — content is NOT stored.
        doc = Document(id=new_id(), case_id=case.id, filename=filename,
                       content_type=ctype, size_bytes=len(content), sha256=sha256,
                       status="quarantined", quarantine_reason=reason, origin=origin,
                       extraction="unsupported", uploaded_by=principal.username)
        db.add(doc)
        db.commit()
        audit.emit(settings, action="document.quarantined", entity_type="document",
                   entity_id=doc.id, principal=principal, case_id=case.id,
                   detail={"filename": filename, "reason": reason, "sha256": sha256,
                           "size_bytes": len(content), **(extra_detail or {})})
        return doc

    # FR-C07: same content already in the case → warn (duplicate_of) and proceed.
    earlier = db.scalar(
        select(Document)
        .where(Document.case_id == case.id, Document.sha256 == sha256,
               Document.status != "quarantined")
        .order_by(Document.uploaded_at.asc())
        .limit(1))

    doc = Document(id=new_id(), case_id=case.id, filename=filename, content_type=ctype,
                   size_bytes=len(content), sha256=sha256, origin=origin,
                   duplicate_of=earlier.id if earlier else None,
                   uploaded_by=principal.username)

    storage.write_blob(doc.id, ext, content)

    text = extract_text(content, ext, max_chars=settings.max_extract_chars)
    if text is None:
        doc.extraction, doc.status = "unsupported", "no_text"
    elif text.strip():
        doc.extraction, doc.status = "ok", "ready"
    else:
        # e.g. scanned/image-only PDF: no text layer (OCR is a documented v1 gap).
        doc.extraction, doc.status = "empty", "no_text"
    if text is not None:
        storage.write_extract(doc.id, text)

    db.add(doc)
    db.commit()

    # Large-document retrieval (RAG): chunk + index the extract so each section
    # can later be grounded on its most relevant passages, not just the first
    # MAX_DOC_CHARS. Gated on the master retrieval mode and fail-open, so an
    # off/document-only deployment is completely unchanged.
    rag_mode = fetch_rag_mode()
    if text and text.strip() and rag_mode != "off":
        indexed = index_document_chunks(db, doc, text, rag_mode)
        if indexed:
            audit.emit(settings, action="document.indexed", entity_type="document",
                       entity_id=doc.id, principal=principal, case_id=case.id,
                       detail={"chunks": indexed, "mode": rag_mode, "chars": len(text)})

    # Auto-tag (fail-open): filename still carries signal even when no text.
    result = classify_document(filename, text or "")
    best = (result or {}).get("best") or None
    tag = None
    if best and best.get("doctype_code"):
        tag = DocumentTag(document_id=doc.id, doctype_code=best["doctype_code"],
                          confidence=best.get("confidence"), source="auto",
                          needs_review=bool(best.get("needs_review")),
                          period_label=period_label)
        db.add(tag)
        db.commit()

    # FR-F01: lineage depends on this detail carrying the content hash.
    audit.emit(settings, action=action, entity_type="document", entity_id=doc.id,
               principal=principal, case_id=case.id,
               detail={"filename": filename, "sha256": sha256, "size_bytes": len(content),
                       "doctype": best.get("doctype_code") if best else None,
                       **(extra_detail or {})})
    if tag is not None:
        audit.emit(settings, action="tag.auto_applied", entity_type="tag",
                   entity_id=tag.id, principal=principal, case_id=case.id,
                   detail={"document_id": doc.id, "doctype_code": tag.doctype_code,
                           "confidence": tag.confidence, "needs_review": tag.needs_review,
                           "method": best.get("method", "keyword")})
    return doc
