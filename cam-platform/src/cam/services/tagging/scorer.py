"""Deterministic, explainable doc-type scoring (FR-C04).

Deliberately not ML: every auto-tag must be explainable to reviewers and MRM
("filename matched phrase 'annual report'"). Doctype masters supply the name /
synonyms / keywords; filename hits outweigh body-text hits; confidence is a
simple saturating normalisation of the raw score.
"""
from __future__ import annotations

import re

FILENAME_HIT_SCORE = 3.0
TEXT_HIT_SCORE = 1.0
TEXT_OCCURRENCE_CAP = 5          # per phrase, so a repeated word cannot dominate
SCORE_NORMALISER = 4.0           # confidence = score / (score + 4.0)
TEXT_WINDOW_CHARS = 6000         # classify on the head of the extract only
MAX_CANDIDATES = 5

_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def tokenize(value: str) -> list[str]:
    """Lowercase, split on any non-alphanumeric run."""
    return [t for t in _SPLIT_RE.split((value or "").lower()) if t]


def _normalise(value: str) -> str:
    """Canonical single-space token stream used for phrase matching."""
    return " ".join(tokenize(value))


def _phrases(doctype: dict) -> set[str]:
    """Matchable phrases for a doctype: each word of its name, plus every
    synonym and keyword as a whole (possibly multi-word) phrase."""
    phrases = set(tokenize(doctype.get("name") or ""))
    for raw in list(doctype.get("synonyms") or []) + list(doctype.get("keywords") or []):
        norm = _normalise(raw)
        if norm:
            phrases.add(norm)
    return phrases


def _occurrences(haystack_norm: str, phrase_norm: str) -> int:
    """Count word-boundary occurrences of a normalised phrase in a normalised
    token stream ('balance sheet' matches only as a contiguous phrase)."""
    padded = f" {haystack_norm} "
    needle = f" {phrase_norm} "
    count = 0
    start = 0
    while True:
        idx = padded.find(needle, start)
        if idx < 0:
            return count
        count += 1
        start = idx + len(needle) - 1  # shared boundary space may start the next hit


def score_doctype(doctype: dict, filename_norm: str, text_norm: str) -> float:
    """Raw score: 3.0 per phrase that hits the filename, 1.0 per text
    occurrence capped at 5 per phrase."""
    score = 0.0
    for phrase in _phrases(doctype):
        if _occurrences(filename_norm, phrase):
            score += FILENAME_HIT_SCORE
        occurrences = _occurrences(text_norm, phrase)
        if occurrences:
            score += TEXT_HIT_SCORE * min(occurrences, TEXT_OCCURRENCE_CAP)
    return score


def confidence_for(score: float) -> float:
    return round(score / (score + SCORE_NORMALISER), 3)


def classify(filename: str, text: str, doctypes: list[dict], threshold: float) -> dict:
    """Contract shape: {candidates, threshold, best} (best null if no hits)."""
    filename_norm = _normalise(filename)
    text_norm = _normalise((text or "")[:TEXT_WINDOW_CHARS])

    scored: list[tuple[float, str]] = []
    for doctype in doctypes or []:
        if not doctype.get("active", True):
            continue
        code = doctype.get("code")
        if not code:
            continue
        score = score_doctype(doctype, filename_norm, text_norm)
        if score > 0:
            scored.append((score, code))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))  # score desc, code asc (deterministic)
    candidates = [{"doctype_code": code, "confidence": confidence_for(score)}
                  for score, code in scored[:MAX_CANDIDATES]]

    best = None
    if candidates:
        best = {**candidates[0], "needs_review": candidates[0]["confidence"] < threshold}
    return {"candidates": candidates, "threshold": threshold, "best": best}
