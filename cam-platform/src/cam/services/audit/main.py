"""audit service — immutable, hash-chained audit trail + lineage composition
(FR-F01..F05, AC-4). Ingest is service-mediated (the common audit client);
reads are RBAC-scoped: analysts see their own actions, reviewers/admins/
auditors see everything (auditor read-only by matrix).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import threading

from fastapi import Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from typing import Any

from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.correlation import get_correlation_id
from cam.common.db import make_engine, make_session_factory, new_id, utcnow
from cam.common.errors import ApiError
from cam.common.rbac import is_own_scoped
from cam.common.security import Principal, make_auth_dependencies

from .models import AuditEvent, Base

settings = get_settings("audit")
engine = make_engine(settings.resolved_db_url())
SessionLocal = make_session_factory(engine)
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM audit service")
_write_lock = threading.Lock()  # single-writer chain integrity within a process


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(engine)


class EventIn(BaseModel):
    action: str
    entity_type: str
    entity_id: str
    case_id: str | None = None
    run_id: str | None = None
    cam_id: str | None = None
    detail: dict[str, Any] = {}
    # Trusted only from service tokens (services emit on behalf of the acting user)
    actor: str | None = None
    actor_roles: list[str] | None = None
    correlation_id: str | None = None


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@app.post("/api/audit/events", status_code=201)
def ingest(body: EventIn, principal: Principal = Depends(current_principal)):
    if principal.is_service:
        actor = body.actor or principal.username
        roles = body.actor_roles if body.actor_roles is not None else ["service"]
    else:
        # end users can never spoof another actor
        actor, roles = principal.username, principal.roles
    with _write_lock, SessionLocal() as db:
        prev = db.scalar(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1))
        event = AuditEvent(
            # id/ts set eagerly: the hash below covers them, so they must exist
            # before flush (column defaults only apply at INSERT time)
            id=new_id(), ts=utcnow(),
            actor=actor, actor_roles=roles, action=body.action,
            entity_type=body.entity_type, entity_id=body.entity_id,
            case_id=body.case_id, run_id=body.run_id, cam_id=body.cam_id,
            correlation_id=body.correlation_id or get_correlation_id(),
            detail=body.detail, prev_hash=prev.hash if prev else "",
        )
        event.hash = hashlib.sha256(
            (event.prev_hash + _canonical(event.core_fields())).encode()
        ).hexdigest()
        db.add(event)
        db.commit()
        return {"id": event.id, "seq": event.seq}


def _scoped_query(principal: Principal):
    q = select(AuditEvent)
    if not principal.is_service and is_own_scoped(principal.roles):
        q = q.where(AuditEvent.actor == principal.username)
    return q


def _apply_filters(q, entity_type=None, entity_id=None, case_id=None, run_id=None,
                   cam_id=None, action=None, actor=None):
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        q = q.where(AuditEvent.entity_id == entity_id)
    if case_id:
        q = q.where(AuditEvent.case_id == case_id)
    if run_id:
        q = q.where(AuditEvent.run_id == run_id)
    if cam_id:
        q = q.where(AuditEvent.cam_id == cam_id)
    if action:
        q = q.where(AuditEvent.action == action)
    if actor:
        q = q.where(AuditEvent.actor == actor)
    return q


@app.get("/api/audit/events")
def list_events(entity_type: str | None = None, entity_id: str | None = None,
                case_id: str | None = None, run_id: str | None = None,
                cam_id: str | None = None, action: str | None = None,
                actor: str | None = None, limit: int = 50, offset: int = 0,
                principal: Principal = Depends(require("audit:read"))):
    limit = max(1, min(limit, 500))
    with SessionLocal() as db:
        q = _apply_filters(_scoped_query(principal), entity_type, entity_id, case_id,
                           run_id, cam_id, action, actor)
        total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
        rows = db.scalars(q.order_by(AuditEvent.seq.desc()).limit(limit).offset(offset)).all()
        return {"events": [e.to_dict() for e in rows], "total": total}


@app.get("/api/audit/lineage/cam/{cam_id}")
def lineage(cam_id: str, principal: Principal = Depends(require("audit:read"))):
    """AC-4: reconstruct a CAM's full lineage from the event stream alone."""
    with SessionLocal() as db:
        cam_events = db.scalars(
            select(AuditEvent).where(AuditEvent.cam_id == cam_id).order_by(AuditEvent.seq)
        ).all()
        if not cam_events:
            raise ApiError.not_found("cam lineage")
        run_ids = {e.run_id for e in cam_events if e.run_id}
        case_ids = {e.case_id for e in cam_events if e.case_id}
        related = db.scalars(
            select(AuditEvent).where(
                (AuditEvent.run_id.in_(run_ids) if run_ids else False)
                | (AuditEvent.case_id.in_(case_ids) if case_ids else False)
            ).order_by(AuditEvent.seq)
        ).all() if (run_ids or case_ids) else []

        seen: set[int] = set()
        timeline = []
        for e in sorted([*related, *cam_events], key=lambda r: r.seq):
            if e.seq not in seen:
                seen.add(e.seq)
                timeline.append(e.to_dict())

        def first_detail(action: str) -> dict | None:
            return next((e["detail"] for e in timeline if e["action"] == action), None)

        return {
            "cam_id": cam_id,
            "run_record": first_detail("run.completed") or first_detail("run.started"),
            "document_hashes": [e["detail"] for e in timeline
                                if e["action"] in ("document.uploaded", "document.pulled")],
            "edits": [e for e in timeline if e["action"].startswith(("cam.", "run.section"))],
            "events": timeline,
        }


@app.get("/api/audit/export")
def export(format: str = "json", case_id: str | None = None, cam_id: str | None = None,
           principal: Principal = Depends(require("audit:read"))):
    with SessionLocal() as db:
        q = _apply_filters(_scoped_query(principal), case_id=case_id, cam_id=cam_id)
        rows = [e.to_dict() for e in db.scalars(q.order_by(AuditEvent.seq)).all()]
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["seq", "ts", "actor", "action", "entity_type", "entity_id",
                         "case_id", "run_id", "cam_id", "correlation_id", "detail", "hash"])
        for r in rows:
            writer.writerow([r["seq"], r["ts"], r["actor"], r["action"], r["entity_type"],
                             r["entity_id"], r["case_id"], r["run_id"], r["cam_id"],
                             r["correlation_id"], json.dumps(r["detail"]), r["hash"]])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=audit_export.csv"})
    return Response(json.dumps(rows, indent=2), media_type="application/json",
                    headers={"Content-Disposition": "attachment; filename=audit_export.json"})


@app.get("/api/audit/verify-chain")
def verify_chain(principal: Principal = Depends(require("audit:read"))):
    with SessionLocal() as db:
        prev_hash = ""
        checked = 0
        for event in db.scalars(select(AuditEvent).order_by(AuditEvent.seq)).all():
            expected = hashlib.sha256((prev_hash + _canonical(event.core_fields())).encode()).hexdigest()
            if event.prev_hash != prev_hash or event.hash != expected:
                return {"intact": False, "checked": checked, "first_break_seq": event.seq}
            prev_hash = event.hash
            checked += 1
        return {"intact": True, "checked": checked, "first_break_seq": None}


@app.get("/api/audit/mrm/sample")
def mrm_sample(n: int = 5, principal: Principal = Depends(require("audit:read"))):
    """FR-F05: random sample of completed runs for periodic output-quality review."""
    with SessionLocal() as db:
        run_ids = [r for r in db.scalars(
            select(AuditEvent.run_id).where(AuditEvent.action == "run.completed").distinct()
        ).all() if r]
    return {"runs": random.sample(run_ids, min(n, len(run_ids))), "population": len(run_ids)}
