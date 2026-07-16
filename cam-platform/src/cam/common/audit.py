"""Audit client: every service emits events to the audit service via the
gateway. Emission is fail-open (a broken audit pipe must not take down
business flow) but failures are logged loudly for ops.
"""
from __future__ import annotations

import logging

from .config import Settings
from .correlation import get_correlation_id
from .http import gateway_client, gateway_headers
from .security import Principal

log = logging.getLogger("cam.audit")


def emit(settings: Settings, *, action: str, entity_type: str, entity_id: str,
         principal: Principal | None = None, case_id: str | None = None,
         run_id: str | None = None, cam_id: str | None = None,
         detail: dict | None = None) -> None:
    event = {
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "case_id": case_id,
        "run_id": run_id,
        "cam_id": cam_id,
        "detail": detail or {},
        # actor of record: the human user where present, else the service
        "actor": principal.username if principal else f"svc:{settings.service_name}",
        "actor_roles": principal.roles if principal else ["service"],
        "correlation_id": get_correlation_id(),
    }
    try:
        with gateway_client(settings, timeout=10.0) as client:
            resp = client.post("/api/audit/events", json=event, headers=gateway_headers(settings))
            if resp.status_code >= 400:
                log.warning("audit emit failed (%s): %s %s", resp.status_code, action, entity_id)
    except Exception as exc:  # pragma: no cover - network failure path
        log.warning("audit emit error: %s %s (%s)", action, entity_id, exc)
