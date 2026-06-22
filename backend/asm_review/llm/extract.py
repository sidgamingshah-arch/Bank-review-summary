"""Per-section structured extraction against Claude.

Each section is one ``messages.parse`` call whose ``output_format`` is that
section's Pydantic model. The (cached) source documents are sent as the prefix of
the user turn; the varying section instruction follows the cache breakpoint.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel

from asm_review.llm.prompts import SECTION_INSTRUCTIONS, SYSTEM_PROMPT
from asm_review.schema.models import SECTION_ORDER, ReviewNote

logger = logging.getLogger(__name__)

# (section_attr, index, total) -> None
ProgressCb = Optional[Callable[[str, int, int], None]]


@dataclass
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    calls: int = 0

    def add(self, usage: Any) -> None:
        if usage is None:
            return
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


def extract_section(
    client: Any,
    *,
    model: str,
    source_blocks: list[dict],
    instruction: str,
    schema_model: type[BaseModel],
    max_tokens: int,
    system: str = SYSTEM_PROMPT,
    usage: Optional[UsageSummary] = None,
) -> BaseModel:
    content = list(source_blocks) + [{"type": "text", "text": instruction}]
    resp = client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": content}],
        output_format=schema_model,
    )
    if usage is not None:
        usage.add(getattr(resp, "usage", None))
    parsed = getattr(resp, "parsed_output", None)
    if parsed is None:
        raise ValueError(
            f"Claude returned no parsed_output for section {schema_model.__name__} "
            f"(stop_reason={getattr(resp, 'stop_reason', None)})"
        )
    return parsed


def run_all_sections(
    client: Any,
    *,
    model: str,
    source_blocks: list[dict],
    max_tokens: int,
    system: str = SYSTEM_PROMPT,
    progress_cb: ProgressCb = None,
) -> tuple[ReviewNote, UsageSummary]:
    usage = UsageSummary()
    collected: dict[str, BaseModel] = {}
    total = len(SECTION_ORDER)
    for idx, (attr, schema_model) in enumerate(SECTION_ORDER, start=1):
        if progress_cb:
            progress_cb(attr, idx, total)
        collected[attr] = extract_section(
            client,
            model=model,
            source_blocks=source_blocks,
            instruction=SECTION_INSTRUCTIONS[attr],
            schema_model=schema_model,
            max_tokens=max_tokens,
            system=system,
            usage=usage,
        )
    note = ReviewNote(**collected)
    logger.info("extraction usage: %s", usage.as_dict())
    return note, usage
