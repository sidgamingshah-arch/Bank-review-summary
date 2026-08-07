"""OCR fallback for scanned / image-only PDFs (closes the FR-C05 gap where
extraction read the text layer only).

Off by default. When ``CAM_OCR_ENABLED=true`` and a PDF yields no extractable
text layer, ``vaf.process_file`` calls :func:`ocr_text` to recover the content.

Providers:
  * ``azure_document_intelligence`` — the Azure Document Intelligence
    ``prebuilt-read`` model over REST (no SDK dependency, easy to mock): submit
    the bytes (202 + Operation-Location), poll until the analyse completes, then
    return ``analyzeResult.content``.
  * ``mock`` — deterministic offline text for dev/tests/demo.

Every call is FAIL-OPEN: on any error it logs and returns ``None`` so intake
degrades to the existing ``no_text`` behaviour — an OCR outage never fails an
upload. The subscription key is read from the env var NAMED by
``ocr_api_key_env`` and never stored on Settings/logged (NFR-06).
"""
from __future__ import annotations

import logging
import os
import time

import httpx

from cam.common.config import get_settings

settings = get_settings("document")
log = logging.getLogger("cam.document.ocr")


def enabled() -> bool:
    return bool(settings.ocr_enabled) and settings.ocr_provider in {"azure_document_intelligence", "mock"}


def _mock(content: bytes) -> str:
    # Deterministic, clearly-synthetic text so dev/test/demo can exercise the
    # OCR path end-to-end without an Azure resource.
    return (f"[OCR MOCK] Recovered text from a scanned document ({len(content)} bytes). "
            "This deterministic placeholder stands in for Azure Document Intelligence "
            "output during development and tests.")


def _azure_document_intelligence(content: bytes, content_type: str | None) -> str | None:
    key = os.environ.get(settings.ocr_api_key_env, "")
    if not settings.ocr_endpoint or not key:
        log.warning("OCR enabled but azure endpoint/key not configured; skipping")
        return None
    base = settings.ocr_endpoint.rstrip("/")
    params = {"api-version": settings.ocr_api_version}
    headers = {"Ocp-Apim-Subscription-Key": key,
               "Content-Type": content_type or "application/pdf"}
    try:
        with httpx.Client(timeout=settings.ocr_timeout_seconds) as c:
            submit = c.post(f"{base}/documentintelligence/documentModels/"
                            f"{settings.ocr_model}:analyze",
                            params=params, headers=headers, content=content)
            if submit.status_code == 200:  # (rare) synchronous result
                return _content_from(submit.json())
            if submit.status_code != 202:
                log.warning("OCR analyse submit failed: %s", submit.status_code)
                return None
            op_url = submit.headers.get("operation-location") or submit.headers.get("Operation-Location")
            if not op_url:
                return None
            poll_headers = {"Ocp-Apim-Subscription-Key": key}
            deadline = time.monotonic() + settings.ocr_poll_max_seconds
            while time.monotonic() < deadline:
                time.sleep(1.0)
                poll = c.get(op_url, headers=poll_headers)
                if poll.status_code >= 400:
                    log.warning("OCR poll failed: %s", poll.status_code)
                    return None
                data = poll.json()
                status = (data.get("status") or "").lower()
                if status == "succeeded":
                    return _content_from(data)
                if status in ("failed", "canceled"):
                    log.warning("OCR analyse status=%s", status)
                    return None
            log.warning("OCR analyse timed out after %ss", settings.ocr_poll_max_seconds)
            return None
    except Exception:
        log.warning("OCR (azure document intelligence) unreachable", exc_info=True)
        return None


def _content_from(data: dict) -> str | None:
    """Pull the flat text out of a Document Intelligence analyse result."""
    result = data.get("analyzeResult") or data.get("analyze_result") or {}
    content = result.get("content")
    if isinstance(content, str) and content.strip():
        return content
    # Fallback: concatenate page lines if 'content' is absent.
    pages = result.get("pages") or []
    lines = [ln.get("content", "") for p in pages for ln in (p.get("lines") or [])]
    joined = "\n".join(l for l in lines if l)
    return joined or None


def ocr_text(content: bytes, content_type: str | None = None) -> str | None:
    """Return OCR-recovered text, or ``None`` (fail-open) when OCR is disabled,
    unconfigured, or errors. Never raises."""
    if not enabled():
        return None
    if settings.ocr_provider == "mock":
        return _mock(content)
    return _azure_document_intelligence(content, content_type)
