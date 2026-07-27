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
