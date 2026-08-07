"""rule service — the standalone rule-composition engine.

Owns "rule processing": composing the layered system-prompt rule stack (house
standing rules -> global standing rules -> agent-role rules -> template
instructions -> preference-derived style directives) into the final system
prompt. It is a pure, stateless transformer over ``cam.common.rules_engine`` —
the same library the genai gateway can run in-process, so output is identical
whether composed here or locally.

Service-to-service only (like the genai gateway): the APIM stand-in blocks
end-user tokens at the edge and each endpoint additionally requires a service
identity.
"""
from __future__ import annotations

from fastapi import Depends
from pydantic import BaseModel

from cam.common import rules_engine
from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.security import Principal, make_auth_dependencies

settings = get_settings("rules")
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM rules")


class AssembleRequest(BaseModel):
    # the rule layers: {global_rules?, template_instructions?}
    layers: dict = {}
    preferences: dict | None = None
    fixed_format: bool = False
    length_guidance: str | None = None
    agent_rules: str | None = None


class AssembleResponse(BaseModel):
    system: str


@app.post("/api/rules/assemble", response_model=AssembleResponse)
def assemble(body: AssembleRequest, principal: Principal = Depends(require_service)) -> AssembleResponse:
    """Compose the layered system-prompt rule stack for one section."""
    system = rules_engine.build_system(
        body.layers, body.preferences, body.fixed_format,
        body.length_guidance, agent_rules=body.agent_rules)
    return AssembleResponse(system=system)


@app.get("/api/rules/house")
def house(principal: Principal = Depends(require_service)) -> dict:
    """The immutable house standing rules and the style-directive vocabulary —
    the fixed rule surface every generation is bound by (FR-D04, NFR-09)."""
    return {
        "house_rules": rules_engine.HOUSE_RULES,
        "style_guardrail": rules_engine.STYLE_GUARDRAIL,
        "vocabulary": {
            "tonality": rules_engine.TONALITY,
            "structure_bias": rules_engine.STRUCTURE,
            "table_usage": rules_engine.TABLES,
            "length": rules_engine.LENGTH,
        },
    }
