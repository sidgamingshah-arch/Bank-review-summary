"""Version-lifecycle engine shared by all five masters.

draft → in_review → published (maker-checker: approver must differ from the
version's maker) with rejected/retired terminal states. Publishing retires the
previous published version. Runtime resolution honours effective-dating
(FR-A06): the published version with the highest version_no whose
effective_from is unset or in the past.
"""
from __future__ import annotations

import difflib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cam.common.db import new_id, utcnow
from cam.common.errors import ApiError

from .models import MasterItem, MasterVersion


def get_item(db: Session, mtype: str, key: str) -> MasterItem | None:
    return db.scalar(select(MasterItem).where(MasterItem.mtype == mtype, MasterItem.key == key))


def require_item(db: Session, mtype: str, key: str) -> MasterItem:
    item = get_item(db, mtype, key)
    if not item:
        raise ApiError.not_found(f"{mtype} '{key}'")
    return item


def item_keys(db: Session, mtype: str) -> set[str]:
    return set(db.scalars(select(MasterItem.key).where(MasterItem.mtype == mtype)).all())


def versions_of(db: Session, item: MasterItem) -> list[MasterVersion]:
    return list(db.scalars(
        select(MasterVersion).where(MasterVersion.item_id == item.id)
        .order_by(MasterVersion.version_no)
    ).all())


def get_version(db: Session, item: MasterItem, version_no: int) -> MasterVersion:
    v = db.scalar(select(MasterVersion).where(
        MasterVersion.item_id == item.id, MasterVersion.version_no == version_no))
    if not v:
        raise ApiError.not_found(f"version {version_no}")
    return v


def published_version(db: Session, item: MasterItem, at: datetime | None = None) -> MasterVersion | None:
    now = at or utcnow()
    candidates = [v for v in versions_of(db, item) if v.status == "published"
                  and (v.effective_from is None or _naive(v.effective_from) <= _naive(now))]
    return max(candidates, key=lambda v: v.version_no) if candidates else None


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None)


def create_item(db: Session, mtype: str, key: str, payload: dict, change_note: str,
                created_by: str) -> tuple[MasterItem, MasterVersion]:
    if get_item(db, mtype, key):
        raise ApiError.conflict(f"{mtype} '{key}' already exists")
    item = MasterItem(id=new_id(), mtype=mtype, key=key)
    db.add(item)
    version = MasterVersion(id=new_id(), item_id=item.id, version_no=1, status="draft",
                            payload=payload, change_note=change_note, created_by=created_by)
    db.add(version)
    db.flush()
    return item, version


def add_version(db: Session, item: MasterItem, payload: dict, change_note: str,
                created_by: str, effective_from: datetime | None = None) -> MasterVersion:
    latest = max((v.version_no for v in versions_of(db, item)), default=0)
    version = MasterVersion(id=new_id(), item_id=item.id, version_no=latest + 1, status="draft",
                            payload=payload, change_note=change_note, created_by=created_by,
                            effective_from=effective_from)
    db.add(version)
    db.flush()
    return version


def submit(db: Session, version: MasterVersion, by: str) -> MasterVersion:
    if version.status != "draft":
        raise ApiError.conflict(f"only draft versions can be submitted (status={version.status})")
    version.status = "in_review"
    version.submitted_by = by
    version.submitted_at = utcnow()
    return version


def approve(db: Session, item: MasterItem, version: MasterVersion, by: str) -> MasterVersion:
    if version.status != "in_review":
        raise ApiError.conflict(f"only in_review versions can be approved (status={version.status})")
    if by in (version.created_by, version.submitted_by):
        # FR-A03: maker-checker — a second admin must approve
        raise ApiError(409, "maker_checker_violation",
                       "approver must be a different admin than the maker")
    previous = published_version(db, item)
    if previous and previous.version_no != version.version_no:
        previous.status = "retired"
    version.status = "published"
    version.approved_by = by
    version.approved_at = utcnow()
    return version


def reject(db: Session, version: MasterVersion, by: str, reason: str) -> MasterVersion:
    if version.status != "in_review":
        raise ApiError.conflict(f"only in_review versions can be rejected (status={version.status})")
    version.status = "rejected"
    version.approved_by = by  # decision-maker of record
    version.approved_at = utcnow()
    version.rejected_reason = reason
    return version


def rollback(db: Session, item: MasterItem, source: MasterVersion, by: str) -> MasterVersion:
    """One-click rollback (FR-A03): clone an older version's payload into a new
    draft, which then walks the normal maker-checker path."""
    return add_version(db, item, dict(source.payload),
                       f"rollback to v{source.version_no}", by)


def canonical(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)


def diff_versions(a: MasterVersion, b: MasterVersion) -> str:
    return "\n".join(difflib.unified_diff(
        canonical(a.payload).splitlines(), canonical(b.payload).splitlines(),
        fromfile=f"v{a.version_no}", tofile=f"v{b.version_no}", lineterm=""))
