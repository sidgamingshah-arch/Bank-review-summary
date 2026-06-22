"""End-to-end pipeline: source files -> extracted ReviewNote -> verified -> .docx.

``client`` can be injected (tests use a fake); otherwise it is built from settings
via the provider layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from asm_review.config import Settings, get_settings
from asm_review.ingest.loader import load_sources
from asm_review.llm.extract import run_all_sections
from asm_review.llm.provider import get_client_and_caps, model_id
from asm_review.llm.reason import apply_reasoning
from asm_review.render.docx_renderer import save_review_note
from asm_review.schema.models import ReviewNote
from asm_review.verify import QCReport, verify_note

logger = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str, int, int], None]]


@dataclass
class PipelineResult:
    note: ReviewNote
    qc: QCReport
    usage: dict
    docx_path: Path
    doc_names: list[str]
    skipped: list[str]


def run_pipeline(
    input_paths: list[str | Path],
    output_path: str | Path,
    *,
    settings: Optional[Settings] = None,
    client: Any = None,
    model: Optional[str] = None,
    progress_cb: ProgressCb = None,
) -> PipelineResult:
    settings = settings or get_settings()

    logger.info("ingesting %d file(s)", len(input_paths))
    sources = load_sources(input_paths, max_pdf_mb=settings.max_pdf_mb)

    if client is None:
        client, _caps = get_client_and_caps(settings)
        model = model or model_id(settings)
    else:
        model = model or settings.model

    note, usage = run_all_sections(
        client,
        model=model,
        source_blocks=sources.blocks,
        max_tokens=settings.max_tokens,
        progress_cb=progress_cb,
    )

    apply_reasoning(note)
    qc = verify_note(note, sources.source_text)

    docx_path = save_review_note(note, output_path, placeholder=settings.placeholder_text)
    logger.info(
        "done: %d/%d fields present, %d verified, cache_read=%d tokens",
        qc.present_fields,
        qc.total_fields,
        qc.verified_fields,
        usage.cache_read_input_tokens,
    )
    return PipelineResult(
        note=note,
        qc=qc,
        usage=usage.as_dict(),
        docx_path=Path(docx_path),
        doc_names=sources.doc_names,
        skipped=sources.skipped,
    )
