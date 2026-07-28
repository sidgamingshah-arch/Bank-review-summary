"""Azure Blob storage backend — routed to a fake in-memory container so the
local disk path stays untouched. Verifies write/read/delete + fail-open read."""
from __future__ import annotations

from types import SimpleNamespace

from cam.services.document import storage


class FakeContainer:
    def __init__(self, store: dict):
        self.store = store

    def upload_blob(self, name, data, overwrite=True):
        self.store[name] = bytes(data)

    def download_blob(self, name):
        if name not in self.store:
            raise KeyError(name)
        return SimpleNamespace(readall=lambda: self.store[name])

    def delete_blob(self, name):
        self.store.pop(name, None)


def test_azure_blob_roundtrip(monkeypatch):
    monkeypatch.setattr(storage.settings, "blob_backend", "azure")
    stores = {storage.settings.azure_blob_container_blobs: {},
              storage.settings.azure_blob_container_extracts: {}}
    monkeypatch.setattr(storage, "_container", lambda name: FakeContainer(stores[name]))

    storage.write_blob("d1", ".pdf", b"BINARYPDF")
    storage.write_extract("d1", "hello extracted text")

    assert stores[storage.settings.azure_blob_container_blobs]["d1.pdf"] == b"BINARYPDF"
    assert storage.read_extract("d1") == "hello extracted text"
    assert storage.read_extract("nonexistent") == ""  # fail-open when missing

    storage.delete_doc("d1", ".pdf")
    assert "d1.pdf" not in stores[storage.settings.azure_blob_container_blobs]
    assert "d1.txt" not in stores[storage.settings.azure_blob_container_extracts]


def test_local_backend_is_default(monkeypatch, tmp_path):
    # Default (no azure) writes to disk and never touches _container.
    monkeypatch.setattr(storage.settings, "blob_backend", "local")
    called = {"azure": 0}
    monkeypatch.setattr(storage, "_container",
                        lambda name: called.__setitem__("azure", called["azure"] + 1))
    storage.write_extract("local-doc", "on disk")
    assert storage.read_extract("local-doc") == "on disk"
    assert called["azure"] == 0
