"""The provenance-carrying field wrapper used throughout the schema.

Every value the model extracts is wrapped in ``Field[T]`` so it carries where it
came from and whether it was actually found. All members are *required* (no
defaults) so the structured-output JSON schema demands the model emit them on
every field; ``value`` and the provenance members are nullable.

``needs_manual_entry`` is intentionally **not** a model-facing field -- it is
derived from ``found``/``value`` so the renderer knows when to drop a
``[To be entered by L1]`` placeholder instead of a real value.
"""

from __future__ import annotations

from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

Confidence = Literal["high", "medium", "low"]


class Field(BaseModel, Generic[T]):
    value: Optional[T]
    found: bool
    confidence: Confidence
    source_document: Optional[str]
    page: Optional[int]
    evidence_quote: Optional[str]

    @property
    def needs_manual_entry(self) -> bool:
        """True when there is nothing trustworthy to render (-> placeholder)."""
        if not self.found or self.value is None:
            return True
        if isinstance(self.value, str) and not self.value.strip():
            return True
        return False

    def display(self) -> Optional[str]:
        """The string to render, or None when a placeholder should be used."""
        if self.needs_manual_entry:
            return None
        return str(self.value)

    # --- convenience constructors (used by tests, reasoning, post-processing) ---
    @classmethod
    def missing(cls) -> "Field[T]":
        return cls(
            value=None,
            found=False,
            confidence="low",
            source_document=None,
            page=None,
            evidence_quote=None,
        )

    @classmethod
    def of(
        cls,
        value: T,
        *,
        confidence: Confidence = "high",
        source_document: Optional[str] = None,
        page: Optional[int] = None,
        evidence_quote: Optional[str] = None,
    ) -> "Field[T]":
        return cls(
            value=value,
            found=True,
            confidence=confidence,
            source_document=source_document,
            page=page,
            evidence_quote=evidence_quote,
        )
