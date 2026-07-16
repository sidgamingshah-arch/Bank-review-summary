"""API gateway — local stand-in for the enterprise APIM (NFR-04).

Emulated APIM policies:
  * single entry point; path-prefix routing to backend services
  * authN enforcement (bearer token must be present and decodable) — fine-grained
    authZ stays with services
  * NFR-10: /api/genai is reachable by service identities only, never end users
  * correlation-id injection (X-Correlation-ID) spanning the whole call chain
  * lightweight per-principal throttling + structured access logging

In production these policies live in the bank's APIM; this component exists so
that the local topology matches the target one (no point-to-point calls).
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, Request, Response

from cam.common.config import get_settings
from cam.common.correlation import CORRELATION_HEADER, CorrelationMiddleware, get_correlation_id
from cam.common.errors import ApiError, install_error_handlers
from cam.common.security import decode_token

log = logging.getLogger("cam.gateway")
settings = get_settings("gateway")

DEFAULT_ROUTES: dict[str, str] = {
    "/api/auth": "http://localhost:8101",
    "/api/masters": "http://localhost:8102",
    "/api/cases": "http://localhost:8103",
    "/api/documents": "http://localhost:8103",
    "/api/tagging": "http://localhost:8104",
    "/api/runs": "http://localhost:8105",
    "/api/genai": "http://localhost:8106",
    "/api/cams": "http://localhost:8107",
    "/api/audit": "http://localhost:8108",
}


def routes() -> dict[str, str]:
    """Route table, overridable per-prefix via env (docker-compose sets these),
    e.g. CAM_ROUTE_MASTERS=http://master-config:8000."""
    table = dict(DEFAULT_ROUTES)
    env_keys = {
        "/api/auth": "CAM_ROUTE_AUTH", "/api/masters": "CAM_ROUTE_MASTERS",
        "/api/cases": "CAM_ROUTE_DOCUMENT", "/api/documents": "CAM_ROUTE_DOCUMENT",
        "/api/tagging": "CAM_ROUTE_TAGGING", "/api/runs": "CAM_ROUTE_ORCHESTRATION",
        "/api/genai": "CAM_ROUTE_GENAI", "/api/cams": "CAM_ROUTE_OUTPUT",
        "/api/audit": "CAM_ROUTE_AUDIT",
    }
    for prefix, env in env_keys.items():
        if os.environ.get(env):
            table[prefix] = os.environ[env]
    return table


ROUTE_TABLE = routes()
OPEN_PATHS = {"/api/auth/token"}  # login is the only unauthenticated API route
RATE_LIMIT = int(os.environ.get("CAM_GATEWAY_RATE_LIMIT_PER_MINUTE", "300"))
_hits: dict[str, deque] = defaultdict(deque)

HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "upgrade",
               "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "content-length", "host"}

app = FastAPI(title="CAM Gateway (APIM stand-in)", version="0.1.0")
app.add_middleware(CorrelationMiddleware)
install_error_handlers(app)

client = httpx.AsyncClient(timeout=180.0)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "gateway", "routes": ROUTE_TABLE}


def _throttle(key: str) -> None:
    now = time.monotonic()
    window = _hits[key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise ApiError(429, "rate_limited", "too many requests, slow down")
    window.append(now)


def _resolve(path: str) -> str:
    for prefix, base in ROUTE_TABLE.items():
        if path == prefix or path.startswith(prefix + "/"):
            return base
    raise ApiError.not_found("route")


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(request: Request, rest: str) -> Response:
    path = "/api/" + rest
    principal_key = "anonymous"

    if path not in OPEN_PATHS:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError.unauthorized()
        principal = decode_token(settings, auth[len("Bearer "):])  # raises 401 on bad/expired
        principal_key = principal.sub
        # APIM policy: model plane is service-to-service only (NFR-10)
        if path.startswith("/api/genai") and not principal.is_service:
            raise ApiError.forbidden("GenAI gateway is not exposed to end users")

    _throttle(principal_key)

    base = _resolve(path)
    url = base + path
    if request.url.query:
        url += "?" + request.url.query

    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    fwd_headers[CORRELATION_HEADER] = get_correlation_id()

    body = await request.body()
    started = time.monotonic()
    try:
        upstream = await client.request(request.method, url, content=body, headers=fwd_headers)
    except httpx.HTTPError as exc:
        log.error("gateway upstream error %s %s: %s", request.method, path, exc)
        raise ApiError(502, "bad_gateway", f"upstream service unavailable for {path}")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info("%s %s -> %s (%dms) corr=%s principal=%s",
             request.method, path, upstream.status_code, elapsed_ms,
             get_correlation_id(), principal_key)

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=resp_headers)
