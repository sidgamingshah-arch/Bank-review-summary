"""Turn uploaded files into Claude content blocks + a verification text corpus.

PDFs are sent as base64 ``document`` blocks (portable across Anthropic direct /
Bedrock / Vertex). Spreadsheets/CSVs are rendered to plain-text tables and sent as
text blocks. A ``cache_control`` breakpoint is placed on the last source block so
the whole document prefix is cached across the per-section calls.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field as dc_field
from pathlib import Path

logger = logging.getLogger(__name__)

PDF_EXTS = {".pdf"}
TABULAR_EXTS = {".xlsx", ".xls", ".csv"}
TEXT_EXTS = {".txt", ".md"}


@dataclass
class LoadedSources:
    blocks: list[dict] = dc_field(default_factory=list)
    source_text: str = ""
    doc_names: list[str] = dc_field(default_factory=list)
    skipped: list[str] = dc_field(default_factory=list)


def _extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts: list[str] = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"[page {i}]\n{text}")
        return "\n\n".join(parts)
    except Exception as exc:  # text extraction is best-effort (scanned PDFs yield none)
        logger.warning("PDF text extraction failed for %s: %s", path.name, exc)
        return ""


def _pdf_block(path: Path, max_pdf_mb: int) -> dict:
    data = path.read_bytes()
    size_mb = len(data) / 1_000_000
    if size_mb > max_pdf_mb:
        raise ValueError(
            f"{path.name} is {size_mb:.1f} MB which exceeds the {max_pdf_mb} MB limit; "
            "split or compress the PDF."
        )
    b64 = base64.standard_b64encode(data).decode("ascii")
    return {
        "type": "document",
        "title": path.name,
        "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
    }


def _tabular_text(path: Path) -> str:
    import pandas as pd

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        return f"Tracker / base-data file: {path.name}\n{df.to_string(index=False)}"

    sheets = pd.read_excel(path, sheet_name=None)
    chunks = [f"Sheet: {name}\n{df.to_string(index=False)}" for name, df in sheets.items()]
    return f"Tracker / base-data file: {path.name}\n" + "\n\n".join(chunks)


def load_sources(paths: list[str | Path], *, max_pdf_mb: int = 28) -> LoadedSources:
    out = LoadedSources()
    text_parts: list[str] = []

    for raw in paths:
        path = Path(raw)
        ext = path.suffix.lower()
        try:
            if ext in PDF_EXTS:
                out.blocks.append(_pdf_block(path, max_pdf_mb))
                text = _extract_pdf_text(path)
                if text:
                    text_parts.append(f"=== {path.name} ===\n{text}")
            elif ext in TABULAR_EXTS:
                text = _tabular_text(path)
                out.blocks.append({"type": "text", "text": text})
                text_parts.append(f"=== {path.name} ===\n{text}")
            elif ext in TEXT_EXTS:
                text = path.read_text(encoding="utf-8", errors="replace")
                labelled = f"Document: {path.name}\n{text}"
                out.blocks.append({"type": "text", "text": labelled})
                text_parts.append(f"=== {path.name} ===\n{text}")
            else:
                out.skipped.append(path.name)
                logger.warning("Skipping unsupported file type: %s", path.name)
                continue
            out.doc_names.append(path.name)
        except Exception as exc:
            out.skipped.append(f"{path.name} ({exc})")
            logger.error("Failed to load %s: %s", path.name, exc)

    if not out.blocks:
        raise ValueError("No usable source documents were provided.")

    # Cache the whole document prefix: breakpoint on the last source block.
    out.blocks[-1] = {**out.blocks[-1], "cache_control": {"type": "ephemeral"}}
    out.source_text = "\n\n".join(text_parts)
    return out
