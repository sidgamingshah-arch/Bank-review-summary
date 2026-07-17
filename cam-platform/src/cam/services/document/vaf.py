"""VAF intake pipeline (FR-C02, FR-C03, FR-C07, NFR-07) — STRICTLY one file
per request.

Steps (synchronous): validate (extension/size/empty) → AV scan → sha256 +
duplicate check → store blob → extract text → auto-tag via tagging service.
Any validation/AV failure persists the Document as ``quarantined`` (the user
must see the reason) but the file content is never stored.
"""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cam.common import audit
from cam.common.config import get_settings
from cam.common.db import new_id
from cam.common.http import gateway_client, gateway_headers
from cam.common.security import Principal

from .extraction import extract_text
from .models import Case, Document, DocumentTag

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


def remove_stored_files(doc: Document) -> None:
    """Delete a document's blob and extract from disk (used by DELETE)."""
    ext = Path(doc.filename).suffix.lower()
    (settings.blob_dir / f"{doc.id}{ext}").unlink(missing_ok=True)
    (settings.extract_dir / f"{doc.id}.txt").unlink(missing_ok=True)


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

    (settings.blob_dir / f"{doc.id}{ext}").write_bytes(content)

    text = extract_text(content, ext)
    if text is None:
        doc.extraction, doc.status = "unsupported", "no_text"
    elif text.strip():
        doc.extraction, doc.status = "ok", "ready"
    else:
        # e.g. scanned/image-only PDF: no text layer (OCR is a documented v1 gap).
        doc.extraction, doc.status = "empty", "no_text"
    if text is not None:
        (settings.extract_dir / f"{doc.id}.txt").write_text(text, encoding="utf-8")

    db.add(doc)
    db.commit()

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
