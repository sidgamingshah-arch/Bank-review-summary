from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from cam.common.db import Base, new_id, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    roles: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def to_dict(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name,
                "email": self.email, "roles": list(self.roles or []), "active": self.active}


class PreferenceProfile(Base):
    """FR-B01/B05: per-user output-preference profile + a single org default
    (row with user_id NULL). Style-only by design — FR-B03 guardrail lives in
    the genai assembly, which never lets preferences touch facts/figures."""

    __tablename__ = "preference_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True, index=True)
    tonality: Mapped[str] = mapped_column(String(16), default="crisp")
    structure_bias: Mapped[str] = mapped_column(String(16), default="paragraphs")
    table_usage: Mapped[str] = mapped_column(String(16), default="auto")
    length: Mapped[str] = mapped_column(String(16), default="standard")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict:
        from cam.common.db import iso
        return {"tonality": self.tonality, "structure_bias": self.structure_bias,
                "table_usage": self.table_usage, "length": self.length,
                "scope": "org_default" if self.user_id is None else "user",
                "updated_at": iso(self.updated_at)}
