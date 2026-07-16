"""Placeholder/variable framework for prompt and template text (FR-A04).

Syntax: ``{{name}}`` for scalar case variables, ``{{doc:<doctype_code>}}`` to
reference a mapped document's content, ``{{industry_kpis}}`` for the KPI
injection block (FR-A11). Validated at save time; resolved at generation time.
"""
from __future__ import annotations

import re

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_:.\-]+)\s*\}\}")

ALLOWED_SCALARS = {
    "borrower_name", "case_type", "relationship", "industry_name",
    "industry_kpis", "today",
}


def find_placeholders(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text or "")


def validate_placeholders(text: str, known_doctype_codes: set[str] | None = None) -> list[str]:
    """Return a list of error strings (empty = valid)."""
    errors: list[str] = []
    for token in find_placeholders(text):
        if token in ALLOWED_SCALARS:
            continue
        if token.startswith("doc:"):
            code = token[4:]
            if known_doctype_codes is not None and code not in known_doctype_codes:
                errors.append(f"unknown document type in placeholder '{{{{{token}}}}}'")
            continue
        errors.append(f"unknown placeholder '{{{{{token}}}}}'")
    return errors


def resolve_placeholders(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    """Substitute known placeholders; return (resolved_text, missing_tokens).

    ``doc:`` placeholders are substituted with a short reference marker — the
    actual document content travels separately as structured grounding blocks
    (never inlined into instructions; NFR-09).
    """
    missing: list[str] = []

    def sub(match: re.Match) -> str:
        token = match.group(1)
        if token in mapping:
            return mapping[token]
        if token.startswith("doc:"):
            return f"[see attached document: {token[4:]}]"
        missing.append(token)
        return "[not available]"

    return PLACEHOLDER_RE.sub(sub, text or ""), missing
