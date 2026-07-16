"""Output/editor service models: CAM documents, per-section version history,
AI suggestions (human-in-the-loop) and the CAM chat transcript.

SQLite + PostgreSQL compatible column types only (NFR-03).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cam.common.db import Base, iso, new_id, utcnow


class Cam(Base):
    """One generated CAM document per completed run (FR-E01)."""

    __tablename__ = "cams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(256))
    template_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | final
    created_by: Mapped[str] = mapped_column(String(64), index=True)  # analyst who ran generation
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finalised_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "case_id": self.case_id, "run_id": self.run_id,
            "title": self.title, "template_key": self.template_key, "status": self.status,
            "created_by": self.created_by, "created_at": iso(self.created_at),
            "finalised_by": self.finalised_by, "finalised_at": iso(self.finalised_at),
        }


class CamSection(Base):
    """A section slot within a CAM; content lives in SectionVersion rows.

    ``section_code == "_gaps"`` is the data-gap trailer (FR-D05): rendered and
    exported, never editable.
    """

    __tablename__ = "cam_sections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cam_id: Mapped[str] = mapped_column(String(36), index=True)
    section_code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256))
    order_no: Mapped[int] = mapped_column(Integer, default=0)
    fixed_format: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def to_dict(self, content: str | None = None) -> dict:
        return {
            "id": self.id, "section_code": self.section_code, "name": self.name,
            "order": self.order_no, "fixed_format": self.fixed_format,
            "current_version_no": self.current_version_no,
            "content": content, "updated_at": iso(self.updated_at),
        }


class SectionVersion(Base):
    """Immutable-ish version history per section (FR-E03). The only mutation
    ever applied is the autosave in-place update of an unnamed manual head
    version by the same author — everything else appends."""

    __tablename__ = "section_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    section_id: Mapped[str] = mapped_column(String(36), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # generated | manual | chat_suggestion | regeneration
    source: Mapped[str] = mapped_column(String(24), default="manual")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    base_version_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def to_dict(self) -> dict:
        """Contract ``SectionVersion`` meta shape (no content)."""
        return {
            "section_id": self.section_id, "version_no": self.version_no,
            "name": self.name, "source": self.source,
            "created_by": self.created_by, "created_at": iso(self.created_at),
        }


class Suggestion(Base):
    """AI-proposed revision (FR-E06): NEVER touches the document except via
    the explicit accept endpoint — human-in-the-loop discipline."""

    __tablename__ = "suggestions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cam_id: Mapped[str] = mapped_column(String(36), index=True)
    section_id: Mapped[str] = mapped_column(String(36), index=True)
    chat_message_id: Mapped[str] = mapped_column(String(36), default="")
    instruction: Mapped[str] = mapped_column(Text, default="")
    proposed_content: Mapped[str] = mapped_column(Text, default="")
    diff: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|accepted|rejected
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "cam_id": self.cam_id, "section_id": self.section_id,
            "status": self.status, "instruction": self.instruction,
            "proposed_content": self.proposed_content, "diff": self.diff,
            "created_at": iso(self.created_at), "decided_by": self.decided_by,
            "decided_at": iso(self.decided_at), "reject_reason": self.reject_reason,
        }


class ChatMessage(Base):
    """CAM conversation transcript (FR-E05): document-level advisory chat and
    section-level revision chat that yields Suggestions."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cam_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="document")  # document | section
    section_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    attached_document_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "cam_id": self.cam_id, "scope": self.scope,
            "section_id": self.section_id, "role": self.role, "content": self.content,
            "attached_document_ids": list(self.attached_document_ids or []),
            "created_at": iso(self.created_at),
        }
