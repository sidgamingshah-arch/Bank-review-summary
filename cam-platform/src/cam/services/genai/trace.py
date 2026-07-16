"""FR-D04 enforcement backstop: after generation, every significant numeric
token in the output must be traceable to the grounding material (or the
resolved case placeholders). Untraceable figures are flagged — they surface in
the run record and the data-gap trailer, never silently accepted.

This is a deterministic heuristic complement to the prompt-level standing
rules, not a replacement for analyst review (the HITL controls in FR-E06).
"""
from __future__ import annotations

import re

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")
ORDERED_LIST_RE = re.compile(r"^\s{0,3}\d+\.\s", re.MULTILINE)


def _normalise(token: str) -> str:
    token = token.replace(",", "").rstrip("%").rstrip(".")
    if token.endswith(".0"):
        token = token[:-2]
    return token


def extract_numbers(text: str) -> set[str]:
    cleaned = ORDERED_LIST_RE.sub("", text or "")  # markdown list markers aren't figures
    out = set()
    for match in NUMBER_RE.findall(cleaned):
        norm = _normalise(match)
        # single digits (list levels, "3 years") create noise, not audit value
        if len(norm.replace(".", "")) >= 2:
            out.add(norm)
    return out


def untraceable_numbers(output: str, grounding_texts: list[str],
                        extra_context: str = "") -> list[str]:
    source_numbers: set[str] = set()
    for text in [*grounding_texts, extra_context]:
        source_numbers |= extract_numbers(text)
    return sorted(extract_numbers(output) - source_numbers)
