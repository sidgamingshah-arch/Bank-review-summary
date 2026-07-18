"""All cross-service calls used by orchestration, isolated behind small
functions so tests can monkeypatch them. Every call goes through the gateway
(NFR-04) with a service token; user-context calls forward the user's token.
"""
from __future__ import annotations

from cam.common.config import get_settings
from cam.common.correlation import CORRELATION_HEADER, get_correlation_id
from cam.common.http import gateway_client, gateway_headers, raise_for_error

settings = get_settings("orchestration")


def _get(path: str, what: str) -> dict:
    with gateway_client(settings) as client:
        resp = client.get(path, headers=gateway_headers(settings))
        raise_for_error(resp, what)
        return resp.json()


def fetch_resolved_template(template_key: str) -> dict:
    return _get(f"/api/masters/resolve/template/{template_key}", "template resolution")


def fetch_kpi_set(industry_code: str) -> dict:
    return _get(f"/api/masters/resolve/kpi-set/{industry_code}", "KPI set resolution")


def fetch_case(case_id: str) -> dict:
    return _get(f"/api/cases/{case_id}", "case lookup")


def fetch_case_documents(case_id: str) -> list[dict]:
    return _get(f"/api/cases/{case_id}/documents", "case documents")


def fetch_document_text(doc_id: str) -> str:
    return _get(f"/api/documents/{doc_id}/text", "document text").get("text", "")


def fetch_user_preferences(user_auth_header: str) -> dict:
    """Run creation resolves the creator's preference profile with THEIR token
    (falls back to the org default inside the auth service)."""
    headers = {"Authorization": user_auth_header}
    cid = get_correlation_id()
    if cid:
        headers[CORRELATION_HEADER] = cid
    with gateway_client(settings) as client:
        resp = client.get("/api/auth/preferences", headers=headers)
        raise_for_error(resp, "preference lookup")
        return resp.json()


def genai_generate(payload: dict) -> dict:
    with gateway_client(settings, timeout=300.0) as client:
        resp = client.post("/api/genai/generate", json=payload,
                           headers=gateway_headers(settings))
        raise_for_error(resp, "genai generate")
        return resp.json()


def create_cam(payload: dict) -> dict:
    with gateway_client(settings) as client:
        resp = client.post("/api/cams", json=payload, headers=gateway_headers(settings))
        raise_for_error(resp, "cam creation")
        return resp.json()


def fetch_cam(cam_id: str) -> dict:
    return _get(f"/api/cams/{cam_id}", "cam lookup")


def push_section_version(cam_id: str, section_id: str, content: str) -> dict:
    with gateway_client(settings) as client:
        resp = client.post(f"/api/cams/{cam_id}/sections/{section_id}/versions",
                           json={"content": content, "source": "regeneration"},
                           headers=gateway_headers(settings))
        raise_for_error(resp, "section version push")
        return resp.json()


def create_cam_section(cam_id: str, payload: dict) -> dict:
    """Late-arrival join: a retried section completes AFTER its CAM exists."""
    with gateway_client(settings) as client:
        resp = client.post(f"/api/cams/{cam_id}/sections", json=payload,
                           headers=gateway_headers(settings))
        raise_for_error(resp, "cam section add")
        return resp.json()


def update_case_status(case_id: str, status: str) -> None:
    """Advisory case-lifecycle notification — fail-open by design: a broken
    status update never blocks or fails a generation run."""
    import logging

    try:
        with gateway_client(settings, timeout=10.0) as client:
            resp = client.patch(f"/api/cases/{case_id}/status", json={"status": status},
                                headers=gateway_headers(settings))
            if resp.status_code >= 400:
                logging.getLogger("cam.orchestration").warning(
                    "case status update failed (%s) for case %s", resp.status_code, case_id)
    except Exception:
        logging.getLogger("cam.orchestration").warning(
            "case status update unreachable for case %s", case_id)
