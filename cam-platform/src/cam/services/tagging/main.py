"""tagging service — auto-classification of intake documents (FR-C04).

Internal endpoint: callers are other services (document intake) via the
gateway; business_admins may also call it directly to test master doctype
configurations. Doctype masters and the confidence threshold come from
master-config and are cached briefly so a burst of uploads does not hammer it.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from fastapi import Depends
from pydantic import BaseModel

from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.errors import ApiError
from cam.common.http import gateway_client, gateway_headers, raise_for_error
from cam.common.security import Principal, make_auth_dependencies

from .scorer import classify

settings = get_settings("tagging")
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM tagging service")

DEFAULT_THRESHOLD = 0.55
# Master changes (doctypes, threshold) reach tagging within this window.
_CACHE_TTL_SECONDS = float(os.environ.get("CAM_TAGGING_CACHE_TTL_SECONDS", "60"))
# key -> (fetched_at_monotonic, value)
_cache: dict[str, tuple[float, Any]] = {}


def fetch_published_doctypes() -> list[dict]:
    """All currently-published doctype payloads from master-config via the
    gateway (monkeypatched in tests)."""
    with gateway_client(settings, timeout=15.0) as client:
        resp = client.get("/api/masters/published/doctypes", headers=gateway_headers(settings))
        raise_for_error(resp, "published doctypes lookup")
        return resp.json()


def fetch_threshold() -> float:
    """tagging_confidence_threshold from master settings via the gateway,
    falling back to the platform default (monkeypatched in tests)."""
    try:
        with gateway_client(settings, timeout=15.0) as client:
            resp = client.get("/api/masters/settings", headers=gateway_headers(settings))
            raise_for_error(resp, "master settings lookup")
            value = resp.json().get("tagging_confidence_threshold")
            return float(value) if value is not None else DEFAULT_THRESHOLD
    except Exception:
        return DEFAULT_THRESHOLD


def _cached(key: str, loader: Callable[[], Any]) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and (now - hit[0]) < _CACHE_TTL_SECONDS:
        return hit[1]
    value = loader()
    _cache[key] = (now, value)
    return value


def llm_classify(filename: str, text: str, doctypes: list[dict]) -> dict | None:
    """FR-C04 fallback: semantic classification via the GenAI gateway when
    name/keyword matching reveals nothing usable. Fail-open — a broken or slow
    model endpoint must never block document intake (monkeypatched in tests)."""
    try:
        payload = {"filename": filename, "text": (text or "")[:6000],
                   "doctypes": [{"code": d.get("code", ""), "name": d.get("name", ""),
                                 "description": d.get("description", ""),
                                 "synonyms": d.get("synonyms") or [],
                                 "keywords": d.get("keywords") or []}
                                for d in doctypes if d.get("code")]}
        with gateway_client(settings, timeout=60.0) as client:
            resp = client.post("/api/genai/classify", json=payload,
                               headers=gateway_headers(settings))
            raise_for_error(resp, "genai classify")
            return resp.json()
    except Exception:
        return None


class ClassifyRequest(BaseModel):
    filename: str = ""
    text: str = ""


@app.post("/api/tagging/classify")
def classify_document(body: ClassifyRequest,
                      principal: Principal = Depends(current_principal)):
    # Internal endpoint: service tokens; business_admins may call for testing.
    if not (principal.is_service or principal.can("masters:settings")):
        raise ApiError.forbidden("internal endpoint (service token or business_admin)")
    doctypes = _cached("doctypes", lambda: fetch_published_doctypes())
    threshold = float(_cached("threshold", lambda: fetch_threshold()))
    result = classify(body.filename, body.text, doctypes, threshold)
    result["llm_consulted"] = False

    # Name/keyword matching is the explainable first pass; when it finds
    # nothing (or only a below-threshold guess), consult the LLM classifier.
    best = result["best"]
    if best is None or best["confidence"] < threshold:
        llm = llm_classify(body.filename, body.text, doctypes)
        result["llm_consulted"] = llm is not None
        if llm and llm.get("code"):
            confidence = round(float(llm.get("confidence", 0.0)), 3)
            if best is None or confidence > best["confidence"]:
                result["best"] = {"doctype_code": llm["code"], "confidence": confidence,
                                  "needs_review": confidence < threshold,
                                  "method": "llm", "rationale": llm.get("rationale", "")}
                others = [c for c in result["candidates"]
                          if c["doctype_code"] != llm["code"]]
                result["candidates"] = [{"doctype_code": llm["code"],
                                         "confidence": confidence}, *others][:5]
    return result
