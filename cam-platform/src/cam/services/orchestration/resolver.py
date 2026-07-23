"""All cross-service calls used by orchestration, isolated behind small
functions so tests can monkeypatch them. Every call goes through the gateway
(NFR-04) with a service token; user-context calls forward the user's token.
"""
from __future__ import annotations

import re

import httpx

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


# external grounding connectors (client-provided, integrated) --------------
_CONNECTOR_DOCTYPES = {"news": "external_news", "search": "external_web"}


def _mock_connector_items(kind: str, borrower: str, industry: str) -> list[dict]:
    """Deterministic stand-in used when a connector is enabled but no endpoint
    URL is configured — lets the 'with connectors' path run and be tested
    offline. Clearly labelled MOCK so it is never mistaken for a live feed."""
    if kind == "news":
        return [{"title": f"{borrower} negative-news screen",
                 "source": "MOCK-NEWS", "date": "recent",
                 "text": (f"Media screen for {borrower}: no adverse regulatory action or "
                          f"default events identified in the {industry or 'sector'} press "
                          "over the trailing 12 months.")}]
    return [{"title": f"{industry or 'sector'} market context",
             "source": "MOCK-WEB", "date": "recent",
             "text": (f"Public market context for {borrower} ({industry or 'sector'}): "
                      "peer set and demand indicators consistent with the case file; "
                      "no contradicting public disclosures found.")}]


def _clean_label_part(value: object) -> str:
    """External connector fields are untrusted: strip anything that could break
    out of the <document label="..."> fence (NFR-09)."""
    return re.sub(r"[<>\r\n\"]+", " ", str(value)).strip()


def fetch_connector_context(kind: str, borrower: str, industry: str,
                            max_items: int | None = None) -> list[dict]:
    """Return external-intelligence grounding docs for one connector kind
    ('news'|'search'). ALWAYS fail-open (never raises, never blocks a run):

      * endpoint URL configured -> POST it through a short timeout; on any
        error, non-200, or malformed body, return [] so the run proceeds on
        case documents alone;
      * no URL configured        -> a deterministic MOCK item (dev/demo).

    The connector is a THIRD-PARTY, out-of-gateway host, so it is called with a
    plain client carrying ONLY its own X-Connector-Key — never the internal
    service token (which would hand a valid platform credential to a vendor,
    NFR-06). Item source/date are sanitised into the label and the text is
    injection-sanitised downstream by the genai gateway.
    """
    import logging
    import os

    log = logging.getLogger("cam.orchestration")
    url = getattr(settings, f"connector_{kind}_url", "") or ""
    cap = max_items or settings.connector_max_items
    doctype = _CONNECTOR_DOCTYPES.get(kind, f"external_{kind}")
    try:
        if url:
            headers = {"Content-Type": "application/json"}
            cid = get_correlation_id()
            if cid:
                headers[CORRELATION_HEADER] = cid
            key = os.environ.get(settings.connector_api_key_env, "")
            if key:
                headers["X-Connector-Key"] = key
            with httpx.Client(timeout=settings.connector_timeout_seconds) as client:
                resp = client.post(url, json={"borrower": borrower, "industry": industry,
                                              "max_items": cap}, headers=headers)
            if resp.status_code >= 400:
                log.warning("connector %s returned %s; proceeding without it",
                            kind, resp.status_code)
                return []
            body = resp.json()
            items = body.get("items", []) if isinstance(body, dict) else []
        else:
            items = _mock_connector_items(kind, borrower, industry)
        if not isinstance(items, list):
            items = []
    except Exception:
        log.warning("connector %s unreachable; proceeding without it", kind)
        return []

    docs = []
    for it in items[:cap]:
        if not isinstance(it, dict):
            continue
        src = _clean_label_part(it.get("source", kind))
        date = _clean_label_part(it.get("date", ""))
        label = f"{kind.upper()}:{src}{(' ' + date) if date else ''}"
        text = str(it.get("text") or it.get("summary") or "").strip()
        if text:
            docs.append({"doctype_code": doctype, "label": label, "text": text})
    return docs


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


def _genai(path: str, payload: dict, what: str) -> dict:
    with gateway_client(settings, timeout=300.0) as client:
        resp = client.post(path, json=payload, headers=gateway_headers(settings))
        raise_for_error(resp, what)
        return resp.json()


def genai_generate(payload: dict) -> dict:
    return _genai("/api/genai/generate", payload, "genai generate (summarisation agent)")


def genai_extract(payload: dict) -> dict:
    return _genai("/api/genai/extract", payload, "genai extract (extraction agent)")


def genai_materiality(payload: dict) -> dict:
    return _genai("/api/genai/materiality", payload, "genai materiality (check agent)")


def genai_consistency(payload: dict) -> dict:
    return _genai("/api/genai/consistency", payload, "genai consistency (check agent)")


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
