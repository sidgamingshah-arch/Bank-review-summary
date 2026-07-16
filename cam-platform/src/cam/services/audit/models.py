from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cam.common.db import Base, iso, new_id, utcnow


class AuditEvent(Base):
    """Append-only, hash-chained event record (FR-F01..F03).

    ``hash = sha256(prev_hash + canonical_json(core_fields))`` makes silent
    tampering detectable (GET /api/audit/verify-chain). There are no UPDATE or
    DELETE code paths on this table anywhere in the platform.
    """

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, default=new_id)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    actor_roles: Mapped[list] = mapped_column(JSON, default=list)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    cam_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")

    def core_fields(self) -> dict:
        return {
            "id": self.id, "ts": iso(self.ts), "actor": self.actor,
            "actor_roles": list(self.actor_roles or []), "action": self.action,
            "entity_type": self.entity_type, "entity_id": self.entity_id,
            "case_id": self.case_id, "run_id": self.run_id, "cam_id": self.cam_id,
            "correlation_id": self.correlation_id, "detail": self.detail or {},
        }

    def to_dict(self) -> dict:
        return {"seq": self.seq, **self.core_fields(),
                "prev_hash": self.prev_hash, "hash": self.hash}
