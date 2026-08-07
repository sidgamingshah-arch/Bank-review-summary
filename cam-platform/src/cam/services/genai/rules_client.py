"""Compose the system-prompt rule stack via the standalone ``rules`` service,
falling back to the in-process rules engine.

FAIL-OPEN by design: if the rules service is disabled
(``CAM_RULES_SERVICE_ENABLED=false``) or unreachable, generation composes the
identical rule stack locally, so a rules-service outage never fails a run and
behaviour is unchanged. This keeps rule composition a first-class standalone
service without adding a hard runtime dependency on it.
"""
from __future__ import annotations

import logging

from cam.common import rules_engine
from cam.common.config import get_settings
from cam.common.http import gateway_client, gateway_headers

settings = get_settings("genai")
log = logging.getLogger("cam.genai.rules")


def compose_system(layers: dict, preferences: dict | None, fixed_format: bool,
                   length_guidance: str | None, agent_rules: str | None = None) -> str:
    if settings.rules_service_enabled:
        try:
            with gateway_client(settings, timeout=5.0) as client:
                resp = client.post(
                    "/api/rules/assemble", headers=gateway_headers(settings),
                    json={"layers": layers, "preferences": preferences,
                          "fixed_format": fixed_format, "length_guidance": length_guidance,
                          "agent_rules": agent_rules})
                if resp.status_code < 400:
                    system = (resp.json() or {}).get("system")
                    if isinstance(system, str) and system:
                        return system
                log.warning("rules service assemble returned %s; composing locally",
                            resp.status_code)
        except Exception:
            log.warning("rules service unreachable; composing locally", exc_info=True)
    return rules_engine.build_system(layers, preferences, fixed_format,
                                     length_guidance, agent_rules=agent_rules)
