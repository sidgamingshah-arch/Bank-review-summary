"""Embedding egress for the document service.

All embedding goes through the genai gateway's single LLM egress (NFR-10):
``POST /api/genai/embed`` with a service token. Used both at intake (to index
document chunks) and at retrieval time (to embed the query). Fail-open — any
error returns ``None`` so intake never fails and retrieval falls back to
full-text grounding (monkeypatched in tests).
"""
from __future__ import annotations

from cam.common.config import get_settings
from cam.common.http import gateway_client, gateway_headers

settings = get_settings("document")


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Return one vector per input text, or ``None`` on any failure. An empty
    input list returns ``[]`` (no call made)."""
    texts = list(texts or [])
    if not texts:
        return []
    try:
        with gateway_client(settings, timeout=120.0) as client:
            resp = client.post("/api/genai/embed", json={"texts": texts},
                               headers=gateway_headers(settings))
            if resp.status_code >= 400:
                return None
            data = resp.json()
        vectors = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            return None
        return vectors
    except Exception:
        return None
