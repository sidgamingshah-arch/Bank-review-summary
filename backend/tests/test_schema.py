"""Schema + Field wrapper behaviour, and JSON-schema generation for a section."""

from __future__ import annotations

from asm_review.schema.fields import Field
from asm_review.schema.models import HeaderBlock, ReviewNote


def test_sample_note_builds(sample_note: ReviewNote) -> None:
    assert isinstance(sample_note, ReviewNote)
    assert sample_note.header.ac_name.value == "Acme Industries Pvt Ltd"


def test_field_present_and_missing() -> None:
    present = Field[str].of("hello")
    assert present.found is True
    assert present.needs_manual_entry is False
    assert present.display() == "hello"

    missing = Field[str].missing()
    assert missing.found is False
    assert missing.needs_manual_entry is True
    assert missing.display() is None


def test_field_blank_string_is_manual() -> None:
    blank = Field[str].of("   ")
    assert blank.found is True
    assert blank.needs_manual_entry is True
    assert blank.display() is None


def test_section_json_schema_is_generatable() -> None:
    # Structured-output relies on a usable JSON schema from the generic Field[str].
    schema = HeaderBlock.model_json_schema()
    assert schema["type"] == "object"
    assert "br_code" in schema["properties"]
