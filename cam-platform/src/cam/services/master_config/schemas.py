"""Type-specific payload validation for the five masters.

Validation runs at save time (FR-A04): structural checks via Pydantic, then
referential checks (placeholders, doc-type codes, prompt bindings) against the
current master catalogue. Errors are returned as a list of human-readable
strings and surfaced as a 422 validation_error.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from cam.common.placeholders import validate_placeholders

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")


class ModelOverrides(BaseModel):
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=64, le=8192)


class PromptPayload(BaseModel):
    section_code: str
    section_name: str
    scope: Literal["section", "global"] = "section"
    prompt_text: str = Field(min_length=10)
    source_doc_types: list[str] = []
    uses_industry_kpis: bool = False
    # when true, and a connector is enabled in settings, this section's
    # extraction grounding is enriched with client-provided news/search context
    uses_external_context: bool = False
    rendering_hints: str = ""
    model_overrides: ModelOverrides | None = None


class TemplateSection(BaseModel):
    order: int = Field(ge=1)
    section_code: str
    mandatory: bool = True
    include_if_doctype: str | None = None
    length_guidance: str = ""
    fixed_format: bool = False
    # Section interlinking (FR-D08): section codes whose generated output is fed
    # in as grounding for this section, and which must finish before it starts.
    # depends_on_all is the executive-summary case: depend on every other section
    # (so it is drafted last, consuming the whole memo). Cycles are rejected.
    depends_on: list[str] = []
    depends_on_all: bool = False


class TemplatePayload(BaseModel):
    name: str = Field(min_length=3)
    segment: Literal["corporate", "fi", "project_finance"]
    relationship: Literal["etb", "ntb"]
    template_instructions: str = ""
    sections: list[TemplateSection] = Field(min_length=1)
    required_doc_types: list[str] = []


class FileConstraints(BaseModel):
    formats: list[str] = []
    max_mb: int = Field(default=25, ge=1, le=100)
    max_count: int = Field(default=10, ge=1, le=50)


class DocTypePayload(BaseModel):
    code: str
    name: str = Field(min_length=2)
    description: str = ""
    synonyms: list[str] = []
    keywords: list[str] = []
    active: bool = True
    file_constraints: FileConstraints | None = None
    feeds_sections: list[str] = []


class IndustryPayload(BaseModel):
    sector_code: str
    sector_name: str = Field(min_length=2)
    industry_code: str
    industry_name: str = Field(min_length=2)


class Kpi(BaseModel):
    code: str
    name: str = Field(min_length=2)
    definition: str = ""
    unit: str = ""
    polarity: Literal["higher_better", "lower_better"]
    benchmark: str | None = None
    sections: list[str] = []


class KpiSetPayload(BaseModel):
    industry_code: str
    kpis: list[Kpi] = Field(min_length=1)


PAYLOAD_MODELS = {
    "prompt": PromptPayload,
    "template": TemplatePayload,
    "doctype": DocTypePayload,
    "industry": IndustryPayload,
    "kpi_set": KpiSetPayload,
}

# which payload field must equal the item key
KEY_FIELD = {"prompt": "section_code", "doctype": "code",
             "industry": "industry_code", "kpi_set": "industry_code"}

GLOBAL_PROMPT_KEY = "global_standing_rules"

# Agent-role standing rules are governed prompt-master entries too (scope
# "global"): business admins tune the agents like any other prompt, under
# maker-checker, and they travel in the export bundle.
AGENT_RULE_KEYS = {
    "extraction": "agent_extraction_rules",
    "summarisation": "agent_summarisation_rules",
    "materiality": "agent_materiality_rules",
    "consistency": "agent_consistency_rules",
}
GLOBAL_PROMPT_KEYS = {GLOBAL_PROMPT_KEY, *AGENT_RULE_KEYS.values()}


def is_section_prompt(key: str) -> bool:
    """A section prompt (keyed by section_code) — as opposed to the global standing
    rules or the agent-role rules. Only section prompts are stored in Opik."""
    return key not in GLOBAL_PROMPT_KEYS


def _resolved_deps(section, code_set: set[str]) -> set[str]:
    """A section's effective dependency set: depends_on_all expands to every
    other section; otherwise the explicit (valid) depends_on codes."""
    if section.depends_on_all:
        return code_set - {section.section_code}
    return {d for d in section.depends_on if d in code_set and d != section.section_code}


def _dependency_cycle_errors(sections, code_set: set[str]) -> list[str]:
    """Reject a section dependency graph that is not a DAG (FR-D08): a cycle
    would deadlock the generation queue. Depends_on_all is included in the graph
    so exec-summary-style 'depend on everything' is validated too."""
    graph = {s.section_code: _resolved_deps(s, code_set) for s in sections}
    # WHITE=unvisited, GREY=on the current DFS stack, BLACK=done
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        state[node] = 1  # grey
        for dep in graph.get(node, ()):  # noqa: SIM110
            if state.get(dep, 0) == 1:
                return True  # back-edge -> cycle
            if state.get(dep, 0) == 0 and visit(dep):
                return True
        state[node] = 2  # black
        return False

    for code in graph:
        if state.get(code, 0) == 0 and visit(code):
            return ["sections: dependency cycle detected in depends_on "
                    "(the section graph must be acyclic)"]
    return []


def validate_payload(mtype: str, key: str, payload: dict, *,
                     doctype_codes: set[str], prompt_keys: set[str],
                     industry_codes: set[str]) -> tuple[dict, list[str]]:
    """Return (normalised_payload, errors)."""
    model = PAYLOAD_MODELS[mtype]
    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:
        return payload, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]

    errors: list[str] = []
    key_field = KEY_FIELD.get(mtype)
    if key_field and getattr(parsed, key_field) != key:
        errors.append(f"payload.{key_field} must equal the item key '{key}'")
    if not SLUG_RE.match(key):
        errors.append("key must be a lowercase slug (a-z, 0-9, '-', '_')")

    if mtype == "prompt":
        errors += validate_placeholders(parsed.prompt_text, doctype_codes)
        for code in parsed.source_doc_types:
            if code not in doctype_codes:
                errors.append(f"source_doc_types: unknown document type '{code}'")
        if parsed.scope == "global" and key not in GLOBAL_PROMPT_KEYS:
            errors.append("global-scope prompts must use one of the reserved keys: "
                          + ", ".join(sorted(GLOBAL_PROMPT_KEYS)))
        if parsed.scope == "section" and key in GLOBAL_PROMPT_KEYS:
            errors.append(f"'{key}' is reserved for a global-scope prompt")

    elif mtype == "template":
        errors += validate_placeholders(parsed.template_instructions, doctype_codes)
        orders = [s.order for s in parsed.sections]
        if len(set(orders)) != len(orders):
            errors.append("sections: order values must be unique")
        codes = [s.section_code for s in parsed.sections]
        if len(set(codes)) != len(codes):
            errors.append("sections: section_code values must be unique")
        code_set = set(codes)
        for s in parsed.sections:
            if s.section_code not in prompt_keys:
                errors.append(f"sections: no prompt-master entry for section '{s.section_code}' (FR-A14)")
            if s.include_if_doctype and s.include_if_doctype not in doctype_codes:
                errors.append(f"sections: unknown include_if_doctype '{s.include_if_doctype}'")
            for dep in s.depends_on:
                if dep == s.section_code:
                    errors.append(f"sections: section '{s.section_code}' cannot depend on itself")
                elif dep not in code_set:
                    errors.append(f"sections: '{s.section_code}' depends_on unknown section '{dep}'")
        errors += _dependency_cycle_errors(parsed.sections, code_set)
        for code in parsed.required_doc_types:
            if code not in doctype_codes:
                errors.append(f"required_doc_types: unknown document type '{code}'")

    elif mtype == "kpi_set":
        if key not in industry_codes:
            errors.append(f"kpi-set industry '{key}' is not in the industry taxonomy (FR-A09)")
        kpi_codes = [k.code for k in parsed.kpis]
        if len(set(kpi_codes)) != len(kpi_codes):
            errors.append("kpis: code values must be unique")

    return parsed.model_dump(), errors
