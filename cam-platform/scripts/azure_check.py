"""Validate the configured Azure resources from INSIDE your environment.

Run this where the CAM_AZURE_* / AZURE_* env vars (and keys) are set — it is the
live counterpart to the mocked unit tests, since Azure cannot be reached from
CI. It exercises each service you have configured and prints PASS/FAIL:

    python scripts/azure_check.py

  * Azure OpenAI chat      (CAM_LLM_PROVIDER=azure)
  * Azure OpenAI embeddings(CAM_GENAI_EMBED_PROVIDER=azure)
  * Azure AI Search        (CAM_RETRIEVAL_BACKEND=azure_search)
  * Azure Blob Storage     (CAM_BLOB_BACKEND=azure)

Only the services you've enabled are probed; the rest are skipped. Exits
non-zero if any enabled check fails. No secrets are printed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cam.common.config import get_settings  # noqa: E402

settings = get_settings("azure-check")
results: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


def check_chat() -> None:
    if settings.llm_provider != "azure":
        print("  skip  Azure OpenAI chat (CAM_LLM_PROVIDER != azure)")
        return
    from cam.services.genai.providers import AzureOpenAIProvider
    try:
        prov = AzureOpenAIProvider(settings)
        res = prov.generate({}, "You are a terse assistant.", "Reply with the single word: pong.")
        _record("Azure OpenAI chat", bool(res.content), f"model={res.model}")
    except Exception as exc:  # noqa: BLE001
        _record("Azure OpenAI chat", False, type(exc).__name__)


def check_embed() -> None:
    if settings.genai_embed_provider != "azure":
        print("  skip  Azure OpenAI embeddings (CAM_GENAI_EMBED_PROVIDER != azure)")
        return
    from cam.services.genai.providers import AzureOpenAIEmbedder
    try:
        emb = AzureOpenAIEmbedder(settings)
        res = emb.embed(["hello from the cam platform"])
        _record("Azure OpenAI embeddings", bool(res.vectors and res.dim), f"dim={res.dim}")
    except Exception as exc:  # noqa: BLE001
        _record("Azure OpenAI embeddings", False, type(exc).__name__)


def check_search() -> None:
    if settings.retrieval_backend != "azure_search":
        print("  skip  Azure AI Search (CAM_RETRIEVAL_BACKEND != azure_search)")
        return
    from cam.services.document import azure_search
    doc_id = "azure-check-doc"
    try:
        dim = settings.genai_embed_dim
        vec = [0.1] * dim
        n = azure_search.upsert_chunks(doc_id, "azure-check-case",
                                       [{"ordinal": 0, "text": "cash flow from operations was strong",
                                         "char_start": 0, "char_end": 37, "vector": vec}])
        hits = azure_search.search_one(doc_id, "cash flow", vec, top_k=1, mode="embedding")
        azure_search.delete_document(doc_id)
        _record("Azure AI Search", n == 1 and len(hits) >= 1, f"index={settings.azure_search_index}")
    except Exception as exc:  # noqa: BLE001
        azure_search.delete_document(doc_id)
        _record("Azure AI Search", False, type(exc).__name__)


def check_blob() -> None:
    if settings.blob_backend != "azure":
        print("  skip  Azure Blob Storage (CAM_BLOB_BACKEND != azure)")
        return
    from cam.services.document import storage
    doc_id = "azure-check-blob"
    try:
        storage.write_blob(doc_id, ".txt", b"binary probe")
        storage.write_extract(doc_id, "extract probe text")
        ok = storage.read_extract(doc_id) == "extract probe text"
        storage.delete_doc(doc_id, ".txt")
        _record("Azure Blob Storage", ok, f"container={settings.azure_blob_container_extracts}")
    except Exception as exc:  # noqa: BLE001
        _record("Azure Blob Storage", False, type(exc).__name__)


def main() -> None:
    print("Azure resource check (only enabled services are probed):\n")
    check_chat()
    check_embed()
    check_search()
    check_blob()
    failed = [n for n, ok, _ in results if not ok]
    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    print("All enabled Azure checks passed." if results else "No Azure services enabled.")


if __name__ == "__main__":
    main()
