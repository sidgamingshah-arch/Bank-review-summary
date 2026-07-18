from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cam.common.db import Base, iso, new_id, utcnow


class Run(Base):
    """One generation run (FR-D01). Immutable snapshot of everything that went
    in — template/prompt/KPI/doctype versions, preferences, gaps — so the audit
    trail can reconstruct exactly what produced the draft (FR-F01/FR-A07)."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    template_key: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | running | complete | partial | failed
    cam_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    borrower_name: Mapped[str] = mapped_column(String(256), default="")
    applied_preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    master_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)  # full resolved bundle snapshot
    model_identity: Mapped[str] = mapped_column(String(128), default="pending")
    gaps: Mapped[list] = mapped_column(JSON, default=list)  # [{doctype_code, reason}]
    proceed_with_gaps: Mapped[bool] = mapped_column(Boolean, default=False)

    def to_dict(self, sections: list["SectionJob"]) -> dict:
        return {
            "id": self.id, "case_id": self.case_id, "template_key": self.template_key,
            "status": self.status, "cam_id": self.cam_id, "created_by": self.created_by,
            "created_at": iso(self.created_at), "correlation_id": self.correlation_id,
            "applied_preferences": self.applied_preferences,
            "master_versions": self.master_versions, "model_identity": self.model_identity,
            "gaps": self.gaps,
            "sections": [s.to_dict() for s in sorted(sections, key=lambda x: x.order_no)],
        }


class SectionJob(Base):
    """Queue row + immutable generated draft for one section (FR-D02/D03)."""

    __tablename__ = "section_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    section_code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160), default="")
    order_no: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="initial")  # initial | regeneration
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # queued | running | complete | failed | skipped
    skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    prompt_version: Mapped[int] = mapped_column(Integer, default=0)
    fixed_format: Mapped[bool] = mapped_column(Boolean, default=False)
    length_guidance: Mapped[str] = mapped_column(String(256), default="")
    input_docs: Mapped[list] = mapped_column(JSON, default=list)  # [{doc_id, doctype_code, label}]
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    untraceable: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict:
        return {
            "section_code": self.section_code, "name": self.name, "order": self.order_no,
            "kind": self.kind, "status": self.status, "skip_reason": self.skip_reason,
            "attempts": self.attempts, "error": self.error,
            "prompt_version": self.prompt_version, "fixed_format": self.fixed_format,
            "input_documents": self.input_docs, "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out, "untraceable": self.untraceable,
            "updated_at": iso(self.updated_at),
        }
