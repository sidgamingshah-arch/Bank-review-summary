"""Provenance verification + QC report.

Walks every ``Field`` in the assembled note and checks the model's
``evidence_quote`` (or, as a fallback, the value itself) against the text
extracted from the source documents. Unverifiable values are not deleted -- they
are downgraded to low confidence and surfaced in a QC report so an operator knows
what to double-check, while the generated .docx stays clean and template-faithful.

If little/no text could be extracted from the sources (e.g. a scanned PDF read by
the model via vision), verification is skipped rather than flagging everything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Optional

from pydantic import BaseModel

from asm_review.schema.fields import Field

_WS = re.compile(r"\s+")
_MIN_CORPUS_CHARS = 200  # below this we assume the sources weren't text-extractable
_MIN_VALUE_MATCH_CHARS = 8  # only fall back to value-matching for non-trivial values


def normalize(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


@dataclass
class QCFlag:
    path: str
    status: str  # "missing" | "unverified"
    message: str


@dataclass
class QCReport:
    flags: list[QCFlag] = dc_field(default_factory=list)
    total_fields: int = 0
    present_fields: int = 0
    verified_fields: int = 0
    verification_performed: bool = True

    @property
    def missing(self) -> list[QCFlag]:
        return [f for f in self.flags if f.status == "missing"]

    @property
    def unverified(self) -> list[QCFlag]:
        return [f for f in self.flags if f.status == "unverified"]

    def as_dict(self) -> dict:
        return {
            "total_fields": self.total_fields,
            "present_fields": self.present_fields,
            "verified_fields": self.verified_fields,
            "verification_performed": self.verification_performed,
            "missing_count": len(self.missing),
            "unverified_count": len(self.unverified),
            "flags": [vars(f) for f in self.flags],
        }


def _walk(obj, path: str, out: list[tuple[str, Field]]) -> None:
    # Field is a BaseModel subclass, so check it first and do not recurse into it.
    if isinstance(obj, Field):
        out.append((path, obj))
        return
    if isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            child = getattr(obj, name)
            _walk(child, f"{path}.{name}" if path else name, out)
        return
    if isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk(item, f"{path}[{i}]", out)
        return
    # scalars / None: nothing to collect


def collect_fields(note: BaseModel) -> list[tuple[str, Field]]:
    out: list[tuple[str, Field]] = []
    _walk(note, "", out)
    return out


def verify_note(note: BaseModel, source_text: Optional[str]) -> QCReport:
    """Verify provenance in place and return a QC report."""
    fields = collect_fields(note)
    corpus = normalize(source_text) if source_text else ""
    can_verify = len(corpus) >= _MIN_CORPUS_CHARS

    report = QCReport(total_fields=len(fields), verification_performed=can_verify)

    for path, field in fields:
        if field.needs_manual_entry:
            report.flags.append(QCFlag(path, "missing", "not found in sources"))
            continue

        report.present_fields += 1
        if not can_verify:
            continue

        value_norm = normalize(str(field.value))
        quote_norm = normalize(field.evidence_quote) if field.evidence_quote else ""

        verified = False
        if quote_norm and quote_norm in corpus:
            verified = True
        elif len(value_norm) >= _MIN_VALUE_MATCH_CHARS and value_norm in corpus:
            verified = True

        if verified:
            report.verified_fields += 1
        else:
            field.confidence = "low"
            reason = (
                "evidence quote not found in source text"
                if field.evidence_quote
                else "no evidence quote provided"
            )
            report.flags.append(QCFlag(path, "unverified", reason))

    return report
