"""Character-window chunking for large-document retrieval (RAG).

Splits an extracted document into overlapping character windows, snapping each
window's end to a nearby paragraph/sentence boundary so passages don't cut
mid-sentence. Pure and deterministic (no I/O, no model calls) so it is unit
tested directly. Chunk offsets (char_start/char_end) are kept for provenance.
"""
from __future__ import annotations

# Boundary markers preferred when trimming a window's tail, best first.
_SENTENCE_SEPS = (". ", ".\n", "; ", "! ", "? ", "?\n", "!\n")


def chunk_text(text: str, *, size: int, overlap: int) -> list[dict]:
    """Split ``text`` into overlapping windows of ~``size`` characters.

    Returns a list of ``{ordinal, char_start, char_end, text}`` dicts in reading
    order. ``overlap`` is clamped to ``[0, size-1]`` so the window always
    advances. A window's end is snapped back to the last paragraph break (or,
    failing that, sentence end) that falls in the window's second half; if no
    boundary is found the window is cut hard at ``size``.
    """
    text = text or ""
    if size <= 0 or not text:
        return []
    overlap = max(0, min(overlap, size - 1))

    chunks: list[dict] = []
    n = len(text)
    start = 0
    ordinal = 0
    half = size / 2.0

    while start < n:
        end = min(start + size, n)
        if end < n:  # not the final window — try to end on a clean boundary
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("\n"))
            if cut < half:
                sentence_cut = -1
                for sep in _SENTENCE_SEPS:
                    p = window.rfind(sep)
                    if p >= 0:
                        sentence_cut = max(sentence_cut, p + len(sep) - 1)
                if sentence_cut > cut:
                    cut = sentence_cut
            if cut >= half:
                end = start + cut + 1

        piece = text[start:end].strip()
        if piece:
            chunks.append({"ordinal": ordinal, "char_start": start,
                           "char_end": end, "text": piece})
            ordinal += 1

        if end >= n:
            break
        start = max(end - overlap, start + 1)  # guarantee forward progress

    return chunks
