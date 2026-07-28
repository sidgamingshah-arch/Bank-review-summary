"""RAG chunking + vector ranking — pure functions, no I/O."""
from __future__ import annotations

import math
from types import SimpleNamespace

from cam.services.document import chunking, retrieval


# ---- chunking -------------------------------------------------------------

def test_chunk_offsets_are_exact_and_ordered():
    text = ("Revenue rose strongly this fiscal year for the whole group.\n\n"
            "Cash flow from operations improved and liquidity is ample now.\n\n"
            "Governance and board matters are described in the closing part.")
    chunks = chunking.chunk_text(text, size=60, overlap=10)
    assert chunks
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))
    for c in chunks:
        assert 0 <= c["char_start"] < c["char_end"] <= len(text)
        # text field is exactly the (stripped) slice its offsets point at
        assert c["text"] == text[c["char_start"]:c["char_end"]].strip()


def test_chunk_covers_to_end_of_text():
    text = " ".join(f"word{i}" for i in range(400))  # no trailing whitespace
    chunks = chunking.chunk_text(text, size=200, overlap=40)
    assert max(c["char_end"] for c in chunks) == len(text)


def test_chunk_empty_and_degenerate_inputs():
    assert chunking.chunk_text("", size=100, overlap=10) == []
    assert chunking.chunk_text("hello world", size=0, overlap=0) == []
    # overlap >= size is clamped so the window always advances (no infinite loop)
    out = chunking.chunk_text("x" * 1000, size=100, overlap=500)
    assert len(out) >= 2


# ---- ranking --------------------------------------------------------------

def _chunk(i, text, emb):
    return SimpleNamespace(chunk_index=i, text=text, char_start=0,
                           char_end=len(text), embedding=emb)


def test_cosine_edge_cases():
    assert retrieval.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert retrieval.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert retrieval.cosine([], [1.0]) == 0.0        # empty
    assert retrieval.cosine([1.0], [1.0, 0.0]) == 0.0  # length mismatch
    assert retrieval.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector


def test_rank_orders_by_similarity_and_respects_top_k():
    query = [1.0, 0.0, 0.0]
    chunks = [_chunk(0, "a", [0.1, 1.0, 0.0]),
              _chunk(1, "b", [1.0, 0.0, 0.0]),
              _chunk(2, "c", [0.6, 0.4, 0.0])]
    hits = retrieval.rank(query, chunks, top_k=2)
    assert [h["ordinal"] for h in hits] == [1, 2]        # most similar first
    assert hits[0]["score"] >= hits[1]["score"]
    assert math.isclose(hits[0]["score"], 1.0, abs_tol=1e-4)


def test_rank_skips_chunks_without_a_vector():
    hits = retrieval.rank([1.0, 0.0],
                          [_chunk(0, "a", None), _chunk(1, "b", [1.0, 0.0])],
                          top_k=5)
    assert [h["ordinal"] for h in hits] == [1]


def test_rank_skips_dimension_mismatched_vectors():
    # Stale corpus: chunks embedded at a different dimension than the query
    # (e.g. the embedding model changed and the docs were not reindexed). These
    # must be skipped entirely so the caller falls back to full-text grounding,
    # NOT returned as 0.0-score "hits" that masquerade as relevant passages.
    query = [1.0, 0.0, 0.0]                       # dim 3
    chunks = [_chunk(0, "a", [1.0, 0.0]), _chunk(1, "b", [0.5, 0.5])]  # dim 2
    assert retrieval.rank(query, chunks, top_k=5) == []


def test_rank_drops_non_positive_scores():
    # An orthogonal (0.0) or all-zero query must not surface document-start
    # filler as a hit — those chunks are dropped so the doc falls back.
    query = [1.0, 0.0]
    chunks = [_chunk(0, "orthogonal", [0.0, 1.0]),   # cosine 0.0 -> dropped
              _chunk(1, "aligned", [1.0, 0.0])]      # cosine 1.0 -> kept
    assert [h["ordinal"] for h in retrieval.rank(query, chunks, top_k=5)] == [1]
    assert retrieval.rank([0.0, 0.0], chunks, top_k=5) == []  # zero query -> no hits


# ---- keyword (lexical) ranking — no embedding model -----------------------

def _kchunk(i, text):
    return _chunk(i, text, None)  # keyword mode stores no vector


def test_rank_keyword_prefers_query_term_matches():
    chunks = [_kchunk(0, "governance and board composition matters"),
              _kchunk(1, "cash flow from operations improved and liquidity is ample"),
              _kchunk(2, "revenue rose strongly this fiscal year")]
    hits = retrieval.rank_keyword("cash flow from operations", chunks, top_k=1)
    assert len(hits) == 1 and "cash flow" in hits[0]["text"].lower()


def test_rank_keyword_drops_non_matches_and_empty_query():
    chunks = [_kchunk(0, "totally unrelated boilerplate text")]
    assert retrieval.rank_keyword("cash flow", chunks, top_k=5) == []   # no overlap
    assert retrieval.rank_keyword("", chunks, top_k=5) == []            # empty query
