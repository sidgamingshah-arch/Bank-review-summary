"""Vector ranking for large-document retrieval (RAG).

Brute-force cosine similarity over a document's stored chunk vectors. The
per-case corpus is bounded (a handful of documents, at most a few thousand
chunks), so an in-Python scan is correct and fast and keeps the vector column a
portable JSON list (SQLite + PostgreSQL) with no pgvector dependency. Pure
functions — unit tested directly against ORM rows or simple stand-ins.
"""
from __future__ import annotations

import math
from typing import Any


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 on any degeneracy.
    Vectors are not assumed pre-normalised."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def rank(query_vec: list[float], chunks: list[Any], top_k: int) -> list[dict]:
    """Score every chunk against ``query_vec`` and return the ``top_k`` highest
    as ``{ordinal, text, score, char_start, char_end}`` dicts (score rounded).
    ``chunks`` are objects exposing ``embedding``/``chunk_index``/``text``/
    ``char_start``/``char_end`` (ORM rows or stand-ins). Chunks without a usable
    vector are skipped."""
    scored: list[tuple[float, Any]] = []
    for c in chunks:
        emb = getattr(c, "embedding", None)
        # Skip chunks with no usable vector OR a stale dimension: if the
        # embedding model/dim changed and the corpus was not reindexed, the
        # query vector and the stored vectors differ in length. cosine() would
        # score every such chunk 0.0, and without this guard rank() would still
        # return the document's opening chunks as "hits" — masquerading as
        # relevant passages and defeating the full-text fallback. Skipping them
        # yields zero hits so the caller falls back to full text (fail-open).
        if not isinstance(emb, list) or len(emb) != len(query_vec):
            continue
        score = cosine(query_vec, emb)
        if score <= 0.0:
            continue  # no positive relevance -> not a hit (honest provenance)
        scored.append((score, c))
    # deterministic ordering: score desc, then chunk_index asc for ties
    scored.sort(key=lambda t: (-t[0], getattr(t[1], "chunk_index", 0)))
    out: list[dict] = []
    for score, c in scored[: max(0, top_k)]:
        out.append({
            "ordinal": getattr(c, "chunk_index", 0),
            "text": getattr(c, "text", ""),
            "score": round(float(score), 4),
            "char_start": getattr(c, "char_start", 0),
            "char_end": getattr(c, "char_end", 0),
        })
    return out
