from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cam.common.db import Base, iso, new_id, utcnow


class Case(Base):
    """Borrower-level container everything hangs off: documents, runs, CAMs
    (FR-C01). Own-scoped for analysts via ``created_by``."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    borrower_name: Mapped[str] = mapped_column(String(256))
    segment: Mapped[str] = mapped_column(String(32))
    relationship: Mapped[str] = mapped_column(String(16))
    industry_code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|generating|finalised
    created_by: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def to_dict(self) -> dict:
        return {"id": self.id, "borrower_name": self.borrower_name,
                "segment": self.segment, "relationship": self.relationship,
                "industry_code": self.industry_code, "status": self.status,
                "created_by": self.created_by, "created_at": iso(self.created_at)}


class Document(Base):
    """Intake record. Binary content and text extracts live in blob storage
    (``.data/blobs`` / ``.data/extracts``), never in the DB (NFR-03).
    Quarantined uploads keep the record (so the user sees the reason) but
    their content is never stored."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="ready")  # quarantined|ready|no_text
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="upload")  # upload|chat|repository
    duplicate_of: Mapped[str | None] = mapped_column(String(36), nullable=True)
    extraction: Mapped[str] = mapped_column(String(16), default="unsupported")  # ok|empty|unsupported
    uploaded_by: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tags: Mapped[list["DocumentTag"]] = relationship(
        "DocumentTag", back_populates="document", cascade="all, delete-orphan",
        order_by="DocumentTag.created_at", lazy="selectin")

    def to_dict(self) -> dict:
        return {"id": self.id, "case_id": self.case_id, "filename": self.filename,
                "content_type": self.content_type, "size_bytes": self.size_bytes,
                "sha256": self.sha256, "status": self.status,
                "quarantine_reason": self.quarantine_reason, "origin": self.origin,
                "duplicate_of": self.duplicate_of, "extraction": self.extraction,
                "uploaded_by": self.uploaded_by, "uploaded_at": iso(self.uploaded_at),
                "tags": [t.to_dict() for t in self.tags]}


class DocumentTag(Base):
    """Doc-type assignment: auto (tagging service, carries confidence and a
    needs_review flag) or user (always confirmed)."""

    __tablename__ = "document_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True)
    doctype_code: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(8), default="user")  # auto|user
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    period_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seq_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped["Document"] = relationship("Document", back_populates="tags")

    def to_dict(self) -> dict:
        return {"id": self.id, "doctype_code": self.doctype_code,
                "confidence": self.confidence, "source": self.source,
                "needs_review": self.needs_review, "period_label": self.period_label,
                "seq_order": self.seq_order, "page_range": self.page_range}
