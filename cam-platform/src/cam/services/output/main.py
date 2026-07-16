"""output service — CAM documents, section versioning/editing, conversational
AI suggestions (human-in-the-loop, FR-E05/E06), finalisation and DOCX/PDF
export (FR-E07/E08). Contract: docs/contracts.md §7 (`/api/cams`).
"""
from __future__ import annotations

import difflib
from typing import Literal

from fastapi import Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from cam.common import audit, rbac
from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.db import make_engine, make_session_factory, utcnow
from cam.common.errors import ApiError
from cam.common.http import gateway_client, gateway_headers, raise_for_error
from cam.common.security import Principal, make_auth_dependencies

from . import exports
from .models import Base, Cam, CamSection, ChatMessage, SectionVersion, Suggestion

settings = get_settings("output")
engine = make_engine(settings.resolved_db_url())
SessionLocal = make_session_factory(engine)
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM output/editor service")

GAP_TRAILER_CODE = "_gaps"
# Roles that may READ cams: editors/downloaders (analyst, reviewer) plus
# auditors/business admins via audit:read; service tokens carry audit:read too.
VIEW_CAPABILITIES = ("cam:edit", "cam:download", "audit:read")


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(engine)


# ---------------------------------------------------------------------------
# Cross-service calls (gateway-mediated, NFR-04). Module-level functions so
# tests can monkeypatch them — other services are not running in unit tests.
# ---------------------------------------------------------------------------

def genai_edit(payload: dict) -> dict:
    """POST /api/genai/edit (contracts.md §6) with a service token."""
    with gateway_client(settings) as client:
        resp = client.post("/api/genai/edit", json=payload, headers=gateway_headers(settings))
        raise_for_error(resp, "genai edit")
        return resp.json()


def fetch_document_text(doc_id: str) -> str:
    """GET /api/documents/{id}/text from the document service."""
    with gateway_client(settings) as client:
        resp = client.get(f"/api/documents/{doc_id}/text", headers=gateway_headers(settings))
        raise_for_error(resp, "document text fetch")
        return resp.json().get("text", "")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class SectionInput(BaseModel):
    section_code: str
    name: str
    order: int
    content: str = ""
    fixed_format: bool = False
    generated: bool = True


class CamCreate(BaseModel):
    case_id: str
    run_id: str
    title: str
    template_key: str
    created_by: str  # the analyst who launched the run — drives own-scoped RBAC
    sections: list[SectionInput]


class SectionEdit(BaseModel):
    content: str
    version_name: str | None = None
    base_version_no: int  # FR-E09 optimistic locking


class RegenerationInput(BaseModel):
    content: str
    source: Literal["regeneration"] = "regeneration"


class ChatInput(BaseModel):
    scope: Literal["document", "section"]
    section_id: str | None = None
    message: str
    attached_document_ids: list[str] = Field(default_factory=list)


class RejectInput(BaseModel):
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_view(principal: Principal) -> None:
    if not any(principal.can(cap) for cap in VIEW_CAPABILITIES):
        raise ApiError.forbidden("requires cam:edit, cam:download or audit:read")


def _get_cam(db, cam_id: str, principal: Principal) -> Cam:
    """Load a cam enforcing own-scoping: analysts only see cams they created
    (404, not 403 — no existence leak across analysts)."""
    cam = db.get(Cam, cam_id)
    if not cam:
        raise ApiError.not_found("cam")
    if rbac.is_own_scoped(principal.roles) and cam.created_by != principal.username:
        raise ApiError.not_found("cam")
    return cam


def _get_section(db, cam: Cam, section_id: str) -> CamSection:
    section = db.get(CamSection, section_id)
    if not section or section.cam_id != cam.id:
        raise ApiError.not_found("section")
    return section


def _sections(db, cam_id: str) -> list[CamSection]:
    return list(db.scalars(
        select(CamSection).where(CamSection.cam_id == cam_id).order_by(CamSection.order_no)
    ).all())


def _head_version(db, section: CamSection) -> SectionVersion | None:
    return db.scalar(select(SectionVersion).where(
        SectionVersion.section_id == section.id,
        SectionVersion.version_no == section.current_version_no,
    ))


def _get_version(db, section_id: str, version_no: int) -> SectionVersion:
    version = db.scalar(select(SectionVersion).where(
        SectionVersion.section_id == section_id,
        SectionVersion.version_no == version_no,
    ))
    if not version:
        raise ApiError.not_found("section version")
    return version


def _cam_json(db, cam: Cam) -> dict:
    out = cam.to_dict()
    out["sections"] = []
    for section in _sections(db, cam.id):
        head = _head_version(db, section)
        out["sections"].append(section.to_dict(content=head.content if head else ""))
    return out


def _ensure_editable(cam: Cam) -> None:
    if cam.status == "final":
        raise ApiError.conflict("cam is finalised and can no longer be modified")


def _unified_diff(a: str, b: str, from_label: str, to_label: str) -> str:
    return "\n".join(difflib.unified_diff(
        (a or "").splitlines(), (b or "").splitlines(),
        fromfile=from_label, tofile=to_label, lineterm="",
    ))


def _append_version(db, section: CamSection, *, content: str, source: str,
                    created_by: str, name: str | None = None) -> SectionVersion:
    version = SectionVersion(
        section_id=section.id, version_no=section.current_version_no + 1,
        content=content, name=name, source=source, created_by=created_by,
        base_version_no=section.current_version_no,
    )
    db.add(version)
    section.current_version_no = version.version_no
    section.updated_at = utcnow()
    return version


# ---------------------------------------------------------------------------
# CAM lifecycle
# ---------------------------------------------------------------------------

@app.post("/api/cams", status_code=201)
def create_cam(body: CamCreate, principal: Principal = Depends(require_service)):
    with SessionLocal() as db:
        cam = Cam(case_id=body.case_id, run_id=body.run_id, title=body.title,
                  template_key=body.template_key, created_by=body.created_by)
        db.add(cam)
        db.flush()  # populate cam.id before wiring sections
        for spec in body.sections:
            section = CamSection(cam_id=cam.id, section_code=spec.section_code,
                                 name=spec.name, order_no=spec.order,
                                 fixed_format=spec.fixed_format, current_version_no=1)
            db.add(section)
            db.flush()  # populate section.id for its v1
            db.add(SectionVersion(
                section_id=section.id, version_no=1, content=spec.content,
                source="generated" if spec.generated else "manual",
                created_by=body.created_by,
            ))
        db.commit()
        audit.emit(settings, action="cam.created", entity_type="cam", entity_id=cam.id,
                   principal=principal, case_id=cam.case_id, run_id=cam.run_id, cam_id=cam.id,
                   detail={"template_key": cam.template_key,
                           "section_codes": [s.section_code for s in body.sections]})
        return _cam_json(db, cam)


@app.get("/api/cams")
def list_cams(case_id: str | None = None,
              principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        stmt = select(Cam).order_by(Cam.created_at)
        if case_id:
            stmt = stmt.where(Cam.case_id == case_id)
        if rbac.is_own_scoped(principal.roles):
            stmt = stmt.where(Cam.created_by == principal.username)
        return [cam.to_dict() for cam in db.scalars(stmt).all()]


@app.get("/api/cams/{cam_id}")
def get_cam(cam_id: str, principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        return _cam_json(db, _get_cam(db, cam_id, principal))


# ---------------------------------------------------------------------------
# Section editing & version history (FR-E02/E03/E09)
# ---------------------------------------------------------------------------

@app.put("/api/cams/{cam_id}/sections/{section_id}")
def edit_section(cam_id: str, section_id: str, body: SectionEdit,
                 principal: Principal = Depends(require("cam:edit"))):
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        section = _get_section(db, cam, section_id)
        _ensure_editable(cam)
        if section.section_code == GAP_TRAILER_CODE:
            raise ApiError.validation("the data-gap trailer section is never editable")
        if body.base_version_no != section.current_version_no:
            raise ApiError.conflict(
                f"section was updated elsewhere (base v{body.base_version_no} "
                f"!= current v{section.current_version_no})")

        head = _head_version(db, section)
        autosave = (head is not None and head.source == "manual" and head.name is None
                    and head.created_by == principal.username and body.version_name is None)
        if autosave:
            # Autosave coalescing: overwrite the same unnamed manual head
            # instead of minting a version per keystroke-save.
            head.content = body.content
            section.updated_at = utcnow()
            version = head
        else:
            version = _append_version(db, section, content=body.content, source="manual",
                                      created_by=principal.username, name=body.version_name)
        db.commit()
        audit.emit(settings, action="cam.section_edited", entity_type="cam_section",
                   entity_id=section.id, principal=principal, case_id=cam.case_id,
                   run_id=cam.run_id, cam_id=cam.id,
                   detail={"section_code": section.section_code,
                           "version_no": version.version_no,
                           "named": body.version_name is not None})
        return {**version.to_dict(), "content": version.content}


@app.get("/api/cams/{cam_id}/sections/{section_id}/versions")
def list_versions(cam_id: str, section_id: str,
                  principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        section = _get_section(db, cam, section_id)
        versions = db.scalars(
            select(SectionVersion).where(SectionVersion.section_id == section.id)
            .order_by(SectionVersion.version_no.desc())
        ).all()
        return [v.to_dict() for v in versions]


@app.get("/api/cams/{cam_id}/sections/{section_id}/versions/{version_no}")
def get_version(cam_id: str, section_id: str, version_no: int,
                principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        section = _get_section(db, cam, section_id)
        version = _get_version(db, section.id, version_no)
        return {**version.to_dict(), "content": version.content}


@app.get("/api/cams/{cam_id}/sections/{section_id}/diff")
def diff_versions(cam_id: str, section_id: str,
                  from_no: int = Query(alias="from"), to_no: int = Query(alias="to"),
                  principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        section = _get_section(db, cam, section_id)
        v_from = _get_version(db, section.id, from_no)
        v_to = _get_version(db, section.id, to_no)
        return {"diff": _unified_diff(v_from.content, v_to.content,
                                      f"v{from_no}", f"v{to_no}")}


@app.post("/api/cams/{cam_id}/sections/{section_id}/versions", status_code=201)
def regenerate_version(cam_id: str, section_id: str, body: RegenerationInput,
                       principal: Principal = Depends(require_service)):
    """Regeneration path: orchestration posts the newly generated content as a
    new version (contracts.md §5); orchestration emits the run.* audit event."""
    with SessionLocal() as db:
        cam = db.get(Cam, cam_id)
        if not cam:
            raise ApiError.not_found("cam")
        section = _get_section(db, cam, section_id)
        _ensure_editable(cam)
        version = _append_version(db, section, content=body.content,
                                  source="regeneration", created_by=principal.username)
        db.commit()
        audit.emit(settings, action="cam.section_edited", entity_type="cam_section",
                   entity_id=section.id, principal=principal, case_id=cam.case_id,
                   run_id=cam.run_id, cam_id=cam.id,
                   detail={"section_code": section.section_code,
                           "version_no": version.version_no, "source": "regeneration"})
        return version.to_dict()


# ---------------------------------------------------------------------------
# Conversational editing (FR-E05/E06)
# ---------------------------------------------------------------------------

def _document_content(db, cam: Cam) -> str:
    parts = []
    for section in _sections(db, cam.id):
        head = _head_version(db, section)
        parts.append(f"## {section.name}\n\n{head.content if head else ''}")
    return "\n\n".join(parts)


@app.post("/api/cams/{cam_id}/chat")
def chat(cam_id: str, body: ChatInput,
         principal: Principal = Depends(require("cam:converse"))):
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        _ensure_editable(cam)

        section = None
        if body.scope == "section":
            if not body.section_id:
                raise ApiError.validation("section_id is required for section-scoped chat")
            section = _get_section(db, cam, body.section_id)
            if section.section_code == GAP_TRAILER_CODE:
                raise ApiError.validation("the data-gap trailer section is never editable")
            head = _head_version(db, section)
            current_content = head.content if head else ""
        else:
            current_content = _document_content(db, cam)

        user_msg = ChatMessage(cam_id=cam.id, scope=body.scope,
                               section_id=section.id if section else None, role="user",
                               content=body.message,
                               attached_document_ids=body.attached_document_ids)
        db.add(user_msg)
        db.flush()  # populate user_msg.id for the suggestion linkage

        payload = {
            "current_content": current_content,
            "instruction": body.message,
            "scope": body.scope,
            "grounding_docs": [
                {"doctype_code": "chat_attachment", "label": doc_id,
                 "text": fetch_document_text(doc_id)}
                for doc_id in body.attached_document_ids
            ],
            "preferences": None,
        }
        reply = genai_edit(payload)
        proposed = reply.get("proposed_content") or ""
        rationale = reply.get("rationale") or ""

        suggestion = None
        if body.scope == "section":
            # FR-E06: AI proposals ALWAYS land as pending suggestions —
            # nothing touches the document except an explicit accept.
            suggestion = Suggestion(
                cam_id=cam.id, section_id=section.id, chat_message_id=user_msg.id,
                instruction=body.message, proposed_content=proposed,
                diff=_unified_diff(current_content, proposed, "current", "proposed"),
            )
            db.add(suggestion)
            assistant_content = rationale or "Proposed revision attached as a tracked suggestion."
        else:
            assistant_content = proposed or rationale

        assistant_msg = ChatMessage(cam_id=cam.id, scope=body.scope,
                                    section_id=section.id if section else None,
                                    role="assistant", content=assistant_content)
        db.add(assistant_msg)
        db.commit()

        audit.emit(settings, action="cam.chat_message", entity_type="chat_message",
                   entity_id=user_msg.id, principal=principal, case_id=cam.case_id,
                   run_id=cam.run_id, cam_id=cam.id,
                   detail={"scope": body.scope,
                           "attached_document_ids": body.attached_document_ids})
        if suggestion:
            audit.emit(settings, action="cam.suggestion_created", entity_type="suggestion",
                       entity_id=suggestion.id, principal=principal, case_id=cam.case_id,
                       run_id=cam.run_id, cam_id=cam.id,
                       detail={"instruction": body.message[:120],
                               "section_code": section.section_code})
        return {"message": user_msg.to_dict(), "reply": assistant_msg.to_dict(),
                "suggestion": suggestion.to_dict() if suggestion else None}


@app.get("/api/cams/{cam_id}/chat")
def list_chat(cam_id: str, section_id: str | None = None,
              principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        stmt = select(ChatMessage).where(ChatMessage.cam_id == cam.id)
        if section_id:
            stmt = stmt.where(ChatMessage.section_id == section_id)
        messages = db.scalars(stmt.order_by(ChatMessage.created_at, ChatMessage.id)).all()
        return [m.to_dict() for m in messages]


@app.get("/api/cams/{cam_id}/suggestions")
def list_suggestions(cam_id: str, status: str | None = None,
                     principal: Principal = Depends(current_principal)):
    _require_view(principal)
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        stmt = select(Suggestion).where(Suggestion.cam_id == cam.id)
        if status:
            stmt = stmt.where(Suggestion.status == status)
        return [s.to_dict() for s in db.scalars(stmt.order_by(Suggestion.created_at)).all()]


def _get_suggestion(db, cam: Cam, suggestion_id: str) -> Suggestion:
    suggestion = db.get(Suggestion, suggestion_id)
    if not suggestion or suggestion.cam_id != cam.id:
        raise ApiError.not_found("suggestion")
    return suggestion


@app.post("/api/cams/{cam_id}/suggestions/{suggestion_id}/accept")
def accept_suggestion(cam_id: str, suggestion_id: str,
                      principal: Principal = Depends(require("cam:edit"))):
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        suggestion = _get_suggestion(db, cam, suggestion_id)
        _ensure_editable(cam)
        if suggestion.status != "pending":
            raise ApiError.conflict(f"suggestion already {suggestion.status}")
        section = _get_section(db, cam, suggestion.section_id)
        version = _append_version(db, section, content=suggestion.proposed_content,
                                  source="chat_suggestion", created_by=principal.username)
        suggestion.status = "accepted"
        suggestion.decided_by = principal.username
        suggestion.decided_at = utcnow()
        db.commit()
        audit.emit(settings, action="cam.suggestion_accepted", entity_type="suggestion",
                   entity_id=suggestion.id, principal=principal, case_id=cam.case_id,
                   run_id=cam.run_id, cam_id=cam.id,
                   detail={"section_code": section.section_code,
                           "version_no": version.version_no})
        return {"suggestion": suggestion.to_dict(), "new_version": version.to_dict()}


@app.post("/api/cams/{cam_id}/suggestions/{suggestion_id}/reject")
def reject_suggestion(cam_id: str, suggestion_id: str, body: RejectInput | None = None,
                      principal: Principal = Depends(require("cam:edit"))):
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        suggestion = _get_suggestion(db, cam, suggestion_id)
        if suggestion.status != "pending":
            raise ApiError.conflict(f"suggestion already {suggestion.status}")
        suggestion.status = "rejected"
        suggestion.decided_by = principal.username
        suggestion.decided_at = utcnow()
        suggestion.reject_reason = body.reason if body else None
        db.commit()
        audit.emit(settings, action="cam.suggestion_rejected", entity_type="suggestion",
                   entity_id=suggestion.id, principal=principal, case_id=cam.case_id,
                   run_id=cam.run_id, cam_id=cam.id,
                   detail={"reason": suggestion.reject_reason})
        return {"suggestion": suggestion.to_dict()}


# ---------------------------------------------------------------------------
# Finalisation & export (FR-E07/E08)
# ---------------------------------------------------------------------------

@app.post("/api/cams/{cam_id}/finalise")
def finalise(cam_id: str, principal: Principal = Depends(require("cam:finalise"))):
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        if cam.status == "final":
            raise ApiError.conflict("cam is already finalised")
        pending = db.scalar(select(Suggestion).where(
            Suggestion.cam_id == cam.id, Suggestion.status == "pending"))
        if pending:
            # Human-in-the-loop discipline: every AI proposal must be
            # explicitly accepted or rejected before sign-off.
            raise ApiError.conflict("pending AI suggestions must be accepted or "
                                    "rejected before finalising")
        cam.status = "final"
        cam.finalised_by = principal.username
        cam.finalised_at = utcnow()
        db.commit()
        audit.emit(settings, action="cam.finalised", entity_type="cam", entity_id=cam.id,
                   principal=principal, case_id=cam.case_id, run_id=cam.run_id,
                   cam_id=cam.id, detail={})
        return _cam_json(db, cam)


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _export(cam_id: str, principal: Principal, fmt: str) -> Response:
    with SessionLocal() as db:
        cam = _get_cam(db, cam_id, principal)
        cam_json = _cam_json(db, cam)
    if fmt == "docx":
        content, media_type = exports.render_docx(cam_json), DOCX_MEDIA_TYPE
    else:
        content, media_type = exports.render_pdf(cam_json), "application/pdf"
    audit.emit(settings, action="cam.exported", entity_type="cam", entity_id=cam.id,
               principal=principal, case_id=cam.case_id, run_id=cam.run_id,
               cam_id=cam.id, detail={"format": fmt})
    filename = f"CAM_{cam.id[:8]}.{fmt}"
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/cams/{cam_id}/export.docx")
def export_docx(cam_id: str, principal: Principal = Depends(require("cam:download"))):
    return _export(cam_id, principal, "docx")


@app.get("/api/cams/{cam_id}/export.pdf")
def export_pdf(cam_id: str, principal: Principal = Depends(require("cam:download"))):
    return _export(cam_id, principal, "pdf")
