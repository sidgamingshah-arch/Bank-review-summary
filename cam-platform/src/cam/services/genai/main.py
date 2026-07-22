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

from . import agents
from .assembly import (CLASSIFY_SYSTEM, build_classify_user, build_edit_user,
                       build_generate_user, build_system)
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
    # agentic pipeline (summarisation role): extraction-agent facts as primary
    # grounding, check-agent feedback for bounded revision loops, governed rules
    extracted_facts: list[dict] = []
    feedback: dict | None = None
    agent_rules: str | None = None


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
                          request["fixed_format"], request["length_guidance"],
                          agent_rules=request.get("agent_rules"))
    user = build_generate_user(request["layers"]["section_prompt"], request["grounding_docs"],
                               request.get("extracted_facts"), request.get("feedback"))
    result = get_provider().generate(request, system, user)

    # FR-D04: numbers in the draft must trace to grounding or case context
    # (KPI benchmarks and placeholder values are legitimate context).
    context = " ".join(str(v) for v in request["placeholders"].values())
    flagged = untraceable_numbers(result.content,
                                  [d["text"] for d in request["grounding_docs"]], context)
    return {"content": result.content, "model": result.model, "usage": result.usage,
            "untraceable_numbers": flagged}


class ExtractRequest(BaseModel):
    section_prompt: str
    grounding_docs: list[GroundingDoc] = []
    placeholders: dict = {}
    agent_rules: str | None = None
    model_overrides: ModelOverrides | None = None


@app.post("/api/genai/extract")
def extract(body: ExtractRequest, principal: Principal = Depends(require_service)):
    """EXTRACTION AGENT: structured, source-attributed facts for one section."""
    request = body.model_dump()
    system = agents.role_system(agents.EXTRACTION_SYSTEM, request.get("agent_rules"))
    user = agents.build_extract_user(request["section_prompt"], request["grounding_docs"])
    result = get_provider().extract(request, system, user)
    parsed = agents.parse_agent_json(result.content, "facts")
    facts = [f for f in (parsed or {}).get("facts", []) if isinstance(f, dict)]
    return {"facts": facts, "parse_ok": parsed is not None,
            "model": result.model, "usage": result.usage}


class MaterialityRequest(BaseModel):
    draft: str
    facts: list[dict] = []
    industry_kpis: str = ""
    section_prompt: str = ""
    agent_rules: str | None = None


@app.post("/api/genai/materiality")
def materiality(body: MaterialityRequest, principal: Principal = Depends(require_service)):
    """MATERIALITY CHECK AGENT: does the draft cover everything material?"""
    request = body.model_dump()
    system = agents.role_system(agents.MATERIALITY_SYSTEM, request.get("agent_rules"))
    user = agents.build_materiality_user(request["draft"], request["facts"],
                                         request["industry_kpis"], request["section_prompt"])
    result = get_provider().materiality(request, system, user)
    parsed = agents.parse_agent_json(result.content, "passed")
    if parsed is None:
        # never invent a verdict: unusable reply -> explicit unchecked outcome
        parsed = {"passed": None, "omissions": [], "flags": [],
                  "notes": "materiality reply was not parseable; check skipped"}
    return {**{"passed": parsed.get("passed"),
               "omissions": [str(o) for o in parsed.get("omissions", [])][:20],
               "flags": [str(f) for f in parsed.get("flags", [])][:20],
               "notes": str(parsed.get("notes", ""))[:300]},
            "model": result.model, "usage": result.usage}


class ConsistencyRequest(BaseModel):
    draft: str
    facts: list[dict] = []
    context: str = ""
    other_sections: dict[str, list[str]] = {}
    agent_rules: str | None = None


@app.post("/api/genai/consistency")
def consistency(body: ConsistencyRequest, principal: Principal = Depends(require_service)):
    """CONSISTENCY CHECK AGENT: draft vs extracted facts vs other sections."""
    request = body.model_dump()
    system = agents.role_system(agents.CONSISTENCY_SYSTEM, request.get("agent_rules"))
    user = agents.build_consistency_user(request["draft"], request["facts"],
                                         request["other_sections"])
    result = get_provider().consistency(request, system, user)
    parsed = agents.parse_agent_json(result.content, "passed")
    if parsed is None:
        parsed = {"passed": None, "inconsistencies": [],
                  "notes": "consistency reply was not parseable; check skipped"}
    return {**{"passed": parsed.get("passed"),
               "inconsistencies": [str(i) for i in parsed.get("inconsistencies", [])][:20],
               "notes": str(parsed.get("notes", ""))[:300]},
            "model": result.model, "usage": result.usage}


class ClassifyDoctype(BaseModel):
    code: str
    name: str = ""
    description: str = ""
    synonyms: list[str] = []
    keywords: list[str] = []


class ClassifyRequest(BaseModel):
    filename: str = ""
    text: str = ""
    doctypes: list[ClassifyDoctype] = Field(min_length=1)


@app.post("/api/genai/classify")
def classify(body: ClassifyRequest, principal: Principal = Depends(require_service)):
    """Semantic document classification against the doc-type master — the
    fallback the tagging service uses when name/keyword matching reveals
    nothing (FR-C04). The model must pick from the supplied catalogue or
    return null; unparseable or invented codes degrade to null (fail-open)."""
    import json

    request = body.model_dump()
    user = build_classify_user(request["filename"], request["text"], request["doctypes"])
    result = get_provider().classify(request, CLASSIFY_SYSTEM, user)

    code, confidence, rationale = None, 0.0, "model reply was not parseable"
    raw = result.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
        valid_codes = {d["code"] for d in request["doctypes"]}
        if parsed.get("code") in valid_codes:
            code = parsed["code"]
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
        rationale = str(parsed.get("rationale", ""))[:300]
    except (ValueError, TypeError, AttributeError):
        pass
    return {"code": code, "confidence": confidence if code else 0.0,
            "rationale": rationale, "model": result.model, "usage": result.usage}


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
