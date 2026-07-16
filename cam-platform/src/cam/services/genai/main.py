"""genai-gateway — the single LLM egress point (NFR-10).

Reachable only with service identities (the APIM stand-in additionally blocks
end-user tokens at the edge). Owns prompt assembly, injection defence, style
rendering and the no-fabrication trace check; callers never talk to a model
endpoint directly.
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, Field

from cam.common.app_factory import create_app
from cam.common.config import get_settings
from cam.common.security import Principal, make_auth_dependencies

from .assembly import build_edit_user, build_generate_user, build_system
from .providers import make_provider
from .trace import untraceable_numbers

settings = get_settings("genai")
current_principal, require, require_service = make_auth_dependencies(settings)

app = create_app(settings, "CAM genai-gateway")

_provider = None


def get_provider():
    global _provider
    if _provider is None:
        _provider = make_provider(settings)
    return _provider


class GroundingDoc(BaseModel):
    doctype_code: str = "unknown"
    label: str = ""
    text: str = ""


class Layers(BaseModel):
    global_rules: str | None = None
    template_instructions: str | None = None
    section_prompt: str


class ModelOverrides(BaseModel):
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=64, le=8192)


class GenerateRequest(BaseModel):
    mode: Literal["section"] = "section"
    layers: Layers
    placeholders: dict = {}
    grounding_docs: list[GroundingDoc] = []
    preferences: dict | None = None
    fixed_format: bool = False
    length_guidance: str | None = None
    model_overrides: ModelOverrides | None = None


class EditRequest(BaseModel):
    current_content: str
    instruction: str = Field(min_length=1)
    scope: Literal["document", "section"] = "section"
    grounding_docs: list[GroundingDoc] = []
    preferences: dict | None = None


@app.post("/api/genai/generate")
def generate(body: GenerateRequest, principal: Principal = Depends(require_service)):
    request = body.model_dump()
    system = build_system(request["layers"], request["preferences"],
                          request["fixed_format"], request["length_guidance"])
    user = build_generate_user(request["layers"]["section_prompt"], request["grounding_docs"])
    result = get_provider().generate(request, system, user)

    # FR-D04: numbers in the draft must trace to grounding or case context
    # (KPI benchmarks and placeholder values are legitimate context).
    context = " ".join(str(v) for v in request["placeholders"].values())
    flagged = untraceable_numbers(result.content,
                                  [d["text"] for d in request["grounding_docs"]], context)
    return {"content": result.content, "model": result.model, "usage": result.usage,
            "untraceable_numbers": flagged}


@app.post("/api/genai/edit")
def edit(body: EditRequest, principal: Principal = Depends(require_service)):
    request = body.model_dump()
    system = build_system({"global_rules": None, "template_instructions": None},
                          request["preferences"], False, None)
    user = build_edit_user(request["current_content"], request["instruction"],
                           request["scope"], request["grounding_docs"])
    result = get_provider().edit(request, system, user)
    return {"proposed_content": result.content, "rationale": result.rationale,
            "model": result.model, "usage": result.usage}
