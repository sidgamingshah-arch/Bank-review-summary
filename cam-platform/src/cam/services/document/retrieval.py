"""Vector + lexical ranking for large-document retrieval (RAG), local backend.

Two ranking modes over a document's stored chunks:
  * embedding — brute-force cosine similarity over stored chunk vectors
  * keyword   — a lexical (tf/overlap) scorer that needs NO embedding model

The per-case corpus is bounded (a handful of documents, at most a few thousand
chunks), so an in-Python scan is correct and fast, and keeps the vector column
a portable JSON list (SQLite + PostgreSQL) with no pgvector dependency. Pure
functions — unit tested directly against ORM rows or simple stand-ins. (The
managed Azure AI Search backend lives in azure_search.py.)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall((text or "").lower())


def _hit(c: Any, score: float) -> dict:
    return {
        "ordinal": getattr(c, "chunk_index", 0),
        "text": getattr(c, "text", ""),
        "score": round(float(score), 4),
        "char_start": getattr(c, "char_start", 0),
        "char_end": getattr(c, "char_end", 0),
    }


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
    """Embedding mode: score every chunk against ``query_vec`` and return the
    ``top_k`` highest as ``{ordinal, text, score, char_start, char_end}`` dicts.
    Chunks without a usable vector — or whose vector dimension differs from the
    query (a stale corpus after an embedding-model change) — are skipped so the
    caller falls back to full-text grounding; non-positive scores are dropped."""
    scored: list[tuple[float, Any]] = []
    for c in chunks:
        emb = getattr(c, "embedding", None)
        if not isinstance(emb, list) or len(emb) != len(query_vec):
            continue
        score = cosine(query_vec, emb)
        if score <= 0.0:
            continue
        scored.append((score, c))
    scored.sort(key=lambda t: (-t[0], getattr(t[1], "chunk_index", 0)))
    return [_hit(c, score) for score, c in scored[: max(0, top_k)]]


def rank_keyword(query: str, chunks: list[Any], top_k: int) -> list[dict]:
    """Keyword mode: rank chunks by lexical overlap with the query — no
    embedding model required. Score rewards matching query terms (by term
    frequency), normalised by chunk length and by the fraction of query terms
    matched, so a passage that mentions more of the query ranks higher. Chunks
    with no overlap are dropped so the caller falls back to full-text."""
    q_terms = {t for t in _tokens(query) if len(t) > 2}
    if not q_terms:
        return []
    scored: list[tuple[float, Any]] = []
    for c in chunks:
        toks = _tokens(getattr(c, "text", ""))
        if not toks:
            continue
        counts = Counter(toks)
        raw = sum(counts[t] for t in q_terms if t in counts)
        if raw <= 0:
            continue
        matched = sum(1 for t in q_terms if t in counts)
        score = (raw / math.sqrt(len(toks))) * (matched / len(q_terms))
        scored.append((score, c))
    scored.sort(key=lambda t: (-t[0], getattr(t[1], "chunk_index", 0)))
    return [_hit(c, score) for score, c in scored[: max(0, top_k)]]
