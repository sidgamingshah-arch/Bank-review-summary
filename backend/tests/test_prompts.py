"""Guard: prompt instructions stay in lockstep with the schema sections."""

from __future__ import annotations

from asm_review.llm.prompts import SECTION_INSTRUCTIONS, SYSTEM_PROMPT
from asm_review.schema.models import SECTION_ORDER


def test_every_section_has_a_nonempty_instruction() -> None:
    for attr, _cls in SECTION_ORDER:
        assert attr in SECTION_INSTRUCTIONS, f"missing instruction for section {attr!r}"
        assert SECTION_INSTRUCTIONS[attr].strip(), f"empty instruction for section {attr!r}"


def test_no_orphan_instructions() -> None:
    section_keys = {attr for attr, _cls in SECTION_ORDER}
    assert set(SECTION_INSTRUCTIONS) == section_keys


def test_system_prompt_is_anchored_to_the_template() -> None:
    assert "ASM Review Note" in SYSTEM_PROMPT
    # the non-standard-PDF mapping discipline must be present
    assert "Map by MEANING" in SYSTEM_PROMPT
    assert "NEVER convert units" in SYSTEM_PROMPT
