"""Blob + text-extract storage: local disk (default) or Azure Blob Storage.

Selected by ``CAM_BLOB_BACKEND`` (local | azure). The document binary and its
text extract are the source-of-truth artefacts, so write failures propagate
(like a local disk error would); reads of a missing extract fail-open to "".
The Azure SDK is imported lazily so it is only required when the azure backend
is actually selected. Credentials come from the env var NAMED by
``azure_blob_connection_env`` (a connection string) or, failing that, AAD /
managed identity against ``azure_blob_account_url`` — never stored on Settings
(NFR-06).
"""
from __future__ import annotations

import os

from cam.common.config import get_settings

settings = get_settings("document")


def _azure() -> bool:
    return settings.blob_backend == "azure"


def _container(name: str):
    """Return a ready Azure ContainerClient (container created if missing)."""
    from azure.storage.blob import BlobServiceClient  # lazy: optional dependency

    conn = os.environ.get(settings.azure_blob_connection_env, "")
    if conn:
        svc = BlobServiceClient.from_connection_string(conn)
    elif settings.azure_blob_account_url:
        from azure.identity import DefaultAzureCredential
        svc = BlobServiceClient(account_url=settings.azure_blob_account_url,
                                credential=DefaultAzureCredential())
    else:
        raise RuntimeError("azure blob backend: set the connection-string env var "
                           f"({settings.azure_blob_connection_env}) or azure_blob_account_url")
    cc = svc.get_container_client(name)
    try:
        cc.create_container()
    except Exception:
        pass  # already exists
    return cc


def write_blob(doc_id: str, ext: str, content: bytes) -> None:
    if _azure():
        _container(settings.azure_blob_container_blobs).upload_blob(
            f"{doc_id}{ext}", content, overwrite=True)
    else:
        (settings.blob_dir / f"{doc_id}{ext}").write_bytes(content)


def write_extract(doc_id: str, text: str) -> None:
    if _azure():
        _container(settings.azure_blob_container_extracts).upload_blob(
            f"{doc_id}.txt", text.encode("utf-8"), overwrite=True)
    else:
        (settings.extract_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")


def read_extract(doc_id: str) -> str:
    """Extracted text for a document, or "" if absent (fail-open)."""
    if _azure():
        try:
            data = _container(settings.azure_blob_container_extracts).download_blob(
                f"{doc_id}.txt").readall()
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""
    p = settings.extract_dir / f"{doc_id}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def delete_doc(doc_id: str, ext: str) -> None:
    if _azure():
        for cname, name in ((settings.azure_blob_container_blobs, f"{doc_id}{ext}"),
                            (settings.azure_blob_container_extracts, f"{doc_id}.txt")):
            try:
                _container(cname).delete_blob(name)
            except Exception:
                pass  # best-effort delete
    else:
        (settings.blob_dir / f"{doc_id}{ext}").unlink(missing_ok=True)
        (settings.extract_dir / f"{doc_id}.txt").unlink(missing_ok=True)
