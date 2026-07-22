from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cam.common.db import Base, iso, new_id, utcnow

# path segment <-> internal master type
MTYPES = {"prompts": "prompt", "templates": "template", "doctypes": "doctype",
          "industries": "industry", "kpi-sets": "kpi_set"}

STATUSES = ("draft", "in_review", "published", "retired", "rejected")


class MasterItem(Base):
    """One configurable object (a prompt, a template, ...) identified by
    (master_type, key). All content lives in versions."""

    __tablename__ = "master_items"
    __table_args__ = (UniqueConstraint("mtype", "key", name="uq_master_item"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    mtype: Mapped[str] = mapped_column(String(16), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MasterVersion(Base):
    """FR-A03/A19: full version lifecycle with maker-checker approval.
    Rows are never edited after leaving draft; publish retires the predecessor."""

    __tablename__ = "master_versions"
    __table_args__ = (UniqueConstraint("item_id", "version_no", name="uq_master_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(String(36), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    submitted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def meta(self) -> dict:
        return {
            "version_no": self.version_no, "status": self.status,
            "created_by": self.created_by, "created_at": iso(self.created_at),
            "submitted_by": self.submitted_by, "approved_by": self.approved_by,
            "approved_at": iso(self.approved_at), "rejected_reason": self.rejected_reason,
            "effective_from": iso(self.effective_from), "change_note": self.change_note,
        }

    def to_dict(self) -> dict:
        return {**self.meta(), "payload": self.payload}


class Setting(Base):
    """Small admin-managed operating parameters (e.g. auto-tag threshold)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)  # {"value": ...} wrapper for scalar safety
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


DEFAULT_SETTINGS: dict[str, object] = {
    "tagging_confidence_threshold": 0.55,
    # ai_first: LLM classifies, keyword scorer corroborates (disagreement flags
    # review). keyword_first: LLM only when keyword matching is weak/absent.
    # keyword_only: never call the model.
    "tagging_mode": "ai_first",
    # agentic generation pipeline (extraction/summarisation always run)
    "agents_materiality_enabled": True,
    "agents_consistency_enabled": True,
    "agent_revision_limit": 1,
}
