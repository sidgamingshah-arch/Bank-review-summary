"""Correlation-ID propagation (NFR-11): the gateway mints X-Correlation-ID,
every service reads it into a contextvar and forwards it on outbound calls
and audit events, so a generation run is traceable end-to-end.
"""
from __future__ import annotations

import contextvars
import uuid

CORRELATION_HEADER = "X-Correlation-ID"
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return _correlation_id.get() or ""


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


class CorrelationMiddleware:
    """Pure-ASGI middleware: adopt inbound correlation id or mint one."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        cid = headers.get(CORRELATION_HEADER.lower().encode(), b"").decode() or str(uuid.uuid4())
        token = _correlation_id.set(cid)

        async def send_with_header(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append(
                    (CORRELATION_HEADER.lower().encode(), cid.encode())
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _correlation_id.reset(token)
