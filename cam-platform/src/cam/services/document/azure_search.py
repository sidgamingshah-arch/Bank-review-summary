"""Azure AI Search retrieval backend (managed vector + keyword/hybrid index).

Selected when ``CAM_RETRIEVAL_BACKEND=azure_search``. Talks to the Search REST
API over httpx (no SDK dependency, easy to mock): create the index if missing,
upsert a document's chunks at intake, query per document (vector / keyword /
hybrid) at generation, and delete a document's chunks on removal.

Every call is FAIL-OPEN: on any error it logs and returns a neutral result so
the caller degrades to local behaviour / full-text grounding — a Search outage
never fails intake or a run. The admin key is read from the env var NAMED by
``azure_search_api_key_env`` and never stored/logged (NFR-06).
"""
from __future__ import annotations

import logging
import os

import httpx

from cam.common.config import get_settings

settings = get_settings("document")
log = logging.getLogger("cam.document.azure_search")

_VECTOR_PROFILE = "cam-vprofile"
_VECTOR_ALGO = "cam-hnsw"


def enabled() -> bool:
    return settings.retrieval_backend == "azure_search" and bool(settings.azure_search_endpoint)


def _client() -> httpx.Client:
    key = os.environ.get(settings.azure_search_api_key_env, "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["api-key"] = key
    return httpx.Client(base_url=settings.azure_search_endpoint.rstrip("/"),
                        headers=headers, timeout=settings.genai_timeout_seconds)


def _qs() -> dict:
    return {"api-version": settings.azure_search_api_version}


def _index_schema(dim: int) -> dict:
    return {
        "name": settings.azure_search_index,
        "fields": [
            {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
            {"name": "doc_id", "type": "Edm.String", "filterable": True},
            {"name": "case_id", "type": "Edm.String", "filterable": True},
            {"name": "ordinal", "type": "Edm.Int32", "sortable": True},
            {"name": "char_start", "type": "Edm.Int32"},
            {"name": "char_end", "type": "Edm.Int32"},
            {"name": "text", "type": "Edm.String", "searchable": True, "analyzer": "standard.lucene"},
            {"name": "vector", "type": "Collection(Edm.Single)", "searchable": True,
             "dimensions": dim, "vectorSearchProfile": _VECTOR_PROFILE},
        ],
        "vectorSearch": {
            "algorithms": [{"name": _VECTOR_ALGO, "kind": "hnsw"}],
            "profiles": [{"name": _VECTOR_PROFILE, "algorithm": _VECTOR_ALGO}],
        },
    }


def ensure_index(dim: int) -> bool:
    """Create the index if it does not exist (idempotent). ``dim`` sets the
    vector field dimension — it must match the embedding model. Returns True if
    the index exists/was created, False on failure (fail-open)."""
    try:
        with _client() as c:
            got = c.get(f"/indexes/{settings.azure_search_index}", params=_qs())
            if got.status_code == 200:
                return True
            resp = c.put(f"/indexes/{settings.azure_search_index}", params=_qs(),
                         json=_index_schema(dim))
            if resp.status_code >= 400:
                log.warning("azure search ensure_index failed: %s", resp.status_code)
                return False
            return True
    except Exception:
        log.warning("azure search ensure_index unreachable", exc_info=True)
        return False


def upsert_chunks(doc_id: str, case_id: str, chunks: list[dict]) -> int:
    """Upsert a document's chunks. Each chunk: {ordinal, text, char_start,
    char_end, vector?}. Ensures the index (dimension from the first vector, else
    the configured embed dim). Returns the count uploaded, 0 on failure."""
    if not chunks:
        return 0
    dim = next((len(c["vector"]) for c in chunks
                if isinstance(c.get("vector"), list)), settings.genai_embed_dim)
    if not ensure_index(dim):
        return 0
    docs = []
    for c in chunks:
        doc = {"@search.action": "mergeOrUpload",
               "id": f"{doc_id}-{c['ordinal']}", "doc_id": doc_id, "case_id": case_id or "",
               "ordinal": int(c["ordinal"]), "char_start": int(c.get("char_start", 0)),
               "char_end": int(c.get("char_end", 0)), "text": c.get("text", "")}
        if isinstance(c.get("vector"), list):
            doc["vector"] = c["vector"]
        docs.append(doc)
    try:
        with _client() as c:
            resp = c.post(f"/indexes/{settings.azure_search_index}/docs/index",
                          params=_qs(), json={"value": docs})
            if resp.status_code >= 400:
                log.warning("azure search upsert failed: %s", resp.status_code)
                return 0
            return len(docs)
    except Exception:
        log.warning("azure search upsert unreachable", exc_info=True)
        return 0


def _hit(row: dict) -> dict:
    return {"ordinal": row.get("ordinal", 0), "text": row.get("text", ""),
            "score": round(float(row.get("@search.score", 0.0)), 4),
            "char_start": row.get("char_start", 0), "char_end": row.get("char_end", 0)}


def search_one(doc_id: str, query: str, query_vec: list[float] | None,
               top_k: int, mode: str) -> list[dict]:
    """Query one document's chunks. mode 'embedding' does a vector search (with
    the query text as a hybrid keyword companion when provided); 'keyword' does
    a BM25 search. Returns ranked hit dicts; [] on failure (fail-open)."""
    body: dict = {"top": top_k, "filter": f"doc_id eq '{doc_id}'",
                  "select": "ordinal,text,char_start,char_end"}
    if mode == "embedding" and isinstance(query_vec, list):
        body["vectorQueries"] = [{"kind": "vector", "vector": query_vec,
                                  "fields": "vector", "k": top_k}]
        if query:
            body["search"] = query  # hybrid: vector + keyword
    else:
        body["search"] = query or "*"
        body["queryType"] = "simple"
    try:
        with _client() as c:
            resp = c.post(f"/indexes/{settings.azure_search_index}/docs/search",
                          params=_qs(), json=body)
            if resp.status_code >= 400:
                log.warning("azure search query failed: %s", resp.status_code)
                return []
            data = resp.json()
        return [_hit(r) for r in (data.get("value") or [])][:top_k]
    except Exception:
        log.warning("azure search query unreachable", exc_info=True)
        return []


def search(doc_ids: list[str], query: str, query_vec: list[float] | None,
           top_k: int, mode: str) -> dict:
    """Query each document independently (no bleed, FR-D03). Returns
    {doc_id: [hits]}."""
    return {doc_id: search_one(doc_id, query, query_vec, top_k, mode) for doc_id in doc_ids}


def delete_document(doc_id: str) -> None:
    """Best-effort removal of a document's chunks from the index (fail-open)."""
    try:
        with _client() as c:
            found = c.post(f"/indexes/{settings.azure_search_index}/docs/search", params=_qs(),
                           json={"filter": f"doc_id eq '{doc_id}'", "select": "id", "top": 1000})
            if found.status_code >= 400:
                return
            ids = [r["id"] for r in (found.json().get("value") or []) if r.get("id")]
            if not ids:
                return
            c.post(f"/indexes/{settings.azure_search_index}/docs/index", params=_qs(),
                   json={"value": [{"@search.action": "delete", "id": i} for i in ids]})
    except Exception:
        log.warning("azure search delete unreachable for %s", doc_id, exc_info=True)
