"""Light post-extraction reasoning / normalisation.

The genuinely reasoning-heavy work (previous-quarter comparison, classifying
critical vs other observations, insurance-adequacy judgements) is done by Claude
via the Section X / V instructions in ``prompts.py`` at extraction time.

This module only normalises what is safe to normalise deterministically. We do
**not** auto-compute Section VIII deviation % from a guessed formula: in a
regulated artifact a wrong number is worse than a blank, and the deviation basis
varies by bank. Deviation % is taken from the source/model, and only its
formatting is tidied. A guarded hook is left for a bank-confirmed formula.
"""

from __future__ import annotations

import re

from asm_review.schema.models import ReviewNote

_PCT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _tidy_percentage(text: str) -> str:
    t = text.strip()
    if _PCT_RE.match(t):  # bare number like "12.5" -> "12.5%"
        return f"{t}%"
    return t


def apply_reasoning(note: ReviewNote) -> ReviewNote:
    """Apply deterministic normalisations in place; returns the same note."""
    for row in note.account_position.rows:
        dp = row.deviation_pct
        if dp.found and isinstance(dp.value, str) and dp.value.strip():
            dp.value = _tidy_percentage(dp.value)
    return note
