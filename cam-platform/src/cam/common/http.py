"""Outbound HTTP: every service-to-service call goes through the gateway
(APIM stand-in) with a service token and the current correlation id (NFR-04).
"""
from __future__ import annotations

import httpx

from .config import Settings
from .correlation import CORRELATION_HEADER, get_correlation_id
from .errors import ApiError
from .security import create_service_token


def gateway_client(settings: Settings, timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(base_url=settings.gateway_url, timeout=timeout)


def gateway_headers(settings: Settings) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_service_token(settings)}"}
    cid = get_correlation_id()
    if cid:
        headers[CORRELATION_HEADER] = cid
    return headers


def raise_for_error(resp: httpx.Response, what: str = "upstream call") -> None:
    if resp.status_code < 400:
        return
    try:
        err = resp.json().get("error", {})
        raise ApiError(resp.status_code, err.get("code", "upstream_error"),
                       f"{what}: {err.get('message', resp.text[:200])}", err.get("details"))
    except ApiError:
        raise
    except Exception:
        raise ApiError(resp.status_code, "upstream_error", f"{what} failed ({resp.status_code})")
