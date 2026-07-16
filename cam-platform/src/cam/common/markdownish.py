"""Tiny markdown-subset block parser shared by the DOCX/PDF exporters.

Supports what generation produces: headings, bullet lists, pipe tables and
paragraphs with **bold** spans. Deliberately not a full markdown engine —
exports must be deterministic and dependency-light.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    kind: str  # heading | bullets | table | paragraph
    text: str = ""
    level: int = 0
    items: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_separator_row(line: str) -> bool:
    s = line.strip().strip("|")
    return bool(s) and all(set(cell.strip()) <= set(":-") and cell.strip() for cell in s.split("|"))


def parse_blocks(markdown: str) -> list[Block]:
    blocks: list[Block] = []
    lines = (markdown or "").splitlines()
    i = 0
    para: list[str] = []

    def flush_para():
        nonlocal para
        if para:
            blocks.append(Block(kind="paragraph", text=" ".join(para).strip()))
            para = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_para()
            i += 1
            continue
        if stripped.startswith("#"):
            flush_para()
            level = len(stripped) - len(stripped.lstrip("#"))
            blocks.append(Block(kind="heading", level=min(level, 4), text=stripped.lstrip("#").strip()))
            i += 1
            continue
        if stripped.startswith(("- ", "* ")):
            flush_para()
            items = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                items.append(lines[i].strip()[2:].strip())
                i += 1
            blocks.append(Block(kind="bullets", items=items))
            continue
        if _is_table_row(stripped):
            flush_para()
            rows = []
            while i < len(lines) and _is_table_row(lines[i].strip()):
                row_line = lines[i].strip()
                if not _is_separator_row(row_line):
                    cells = [c.strip() for c in row_line.strip("|").split("|")]
                    rows.append(cells)
                i += 1
            if rows:
                blocks.append(Block(kind="table", rows=rows))
            continue
        para.append(stripped)
        i += 1
    flush_para()
    return blocks


def strip_bold(text: str) -> str:
    return text.replace("**", "")


def bold_spans(text: str) -> list[tuple[str, bool]]:
    """Split ``a **b** c`` into [(a, False), (b, True), (c, False)]."""
    parts = text.split("**")
    return [(part, idx % 2 == 1) for idx, part in enumerate(parts) if part != ""]
