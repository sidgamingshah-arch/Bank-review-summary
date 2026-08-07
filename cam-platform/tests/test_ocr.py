"""OCR fallback for scanned/image-only PDFs: the mock provider, the Azure
Document Intelligence REST flow (mocked with httpx.MockTransport), fail-open,
and the vaf intake wiring (empty text layer -> OCR -> extraction 'ocr')."""
from __future__ import annotations

import io

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_user_headers

from cam.services.document import main as doc_main
from cam.services.document import ocr, vaf

ANALYST1 = make_user_headers("analyst1", ["analyst"])


@pytest.fixture()
def ocr_mock(monkeypatch):
    monkeypatch.setattr(ocr.settings, "ocr_enabled", True)
    monkeypatch.setattr(ocr.settings, "ocr_provider", "mock")


@pytest.fixture()
def ocr_azure(monkeypatch):
    s = ocr.settings
    monkeypatch.setattr(s, "ocr_enabled", True)
    monkeypatch.setattr(s, "ocr_provider", "azure_document_intelligence")
    monkeypatch.setattr(s, "ocr_endpoint", "https://di.cognitiveservices.azure.com")
    monkeypatch.setattr(s, "ocr_api_version", "2024-11-30")
    monkeypatch.setattr(s, "ocr_api_key_env", "CAM_OCR_KEY_TEST")
    monkeypatch.setattr(s, "ocr_model", "prebuilt-read")
    monkeypatch.setenv("CAM_OCR_KEY_TEST", "di-secret")
    monkeypatch.setattr(ocr.time, "sleep", lambda *_a, **_k: None)  # don't actually wait


def _mock_transport(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(ocr.httpx, "Client",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(ocr.settings, "ocr_enabled", False)
    assert ocr.enabled() is False
    assert ocr.ocr_text(b"%PDF-1.4 scan") is None


def test_mock_provider_returns_deterministic_text(ocr_mock):
    assert ocr.enabled() is True
    out = ocr.ocr_text(b"%PDF-1.4 scanned bytes")
    assert out and "OCR MOCK" in out
    assert ocr.ocr_text(b"%PDF-1.4 scanned bytes") == out  # deterministic


def test_azure_document_intelligence_flow(ocr_azure):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and ":analyze" in str(request.url):
            seen["key"] = request.headers.get("Ocp-Apim-Subscription-Key")
            seen["ctype"] = request.headers.get("Content-Type")
            return httpx.Response(202, headers={
                "operation-location": "https://di.cognitiveservices.azure.com/op/123"})
        # poll
        return httpx.Response(200, json={"status": "succeeded",
                                         "analyzeResult": {"content": "Recovered scanned text."}})

    with pytest.MonkeyPatch.context() as mp:
        _mock_transport(mp, handler)
        out = ocr.ocr_text(b"%PDF-1.4 scan", "application/pdf")
    assert out == "Recovered scanned text."
    assert seen["key"] == "di-secret"
    assert seen["ctype"] == "application/pdf"


def test_azure_fail_open_on_error(ocr_azure):
    def handler(request):
        raise httpx.ConnectError("document intelligence down")

    with pytest.MonkeyPatch.context() as mp:
        _mock_transport(mp, handler)
        assert ocr.ocr_text(b"%PDF-1.4 scan", "application/pdf") is None


def test_azure_unconfigured_returns_none(monkeypatch):
    monkeypatch.setattr(ocr.settings, "ocr_enabled", True)
    monkeypatch.setattr(ocr.settings, "ocr_provider", "azure_document_intelligence")
    monkeypatch.setattr(ocr.settings, "ocr_endpoint", "")  # no endpoint
    assert ocr.ocr_text(b"scan", "application/pdf") is None


# ------------------------------------------------------------- intake wiring

def _make_case(client):
    r = client.post("/api/cases", json={"borrower_name": "Scan Co", "segment": "corporate",
                                        "relationship": "etb", "industry_code": "IND-STEEL"},
                    headers=ANALYST1)
    assert r.status_code == 201, r.text
    return r.json()


def test_scanned_pdf_recovered_via_ocr(monkeypatch, ocr_mock):
    # tagging + rag isolated; text layer is empty (scanned PDF)
    monkeypatch.setattr(vaf, "classify_document", lambda filename, text: None)
    monkeypatch.setattr(vaf, "extract_text", lambda content, ext, max_chars=0: "")
    with TestClient(doc_main.app) as c:
        case = _make_case(c)
        r = c.post(f"/api/cases/{case['id']}/documents",
                   files={"file": ("scan.pdf", io.BytesIO(b"%PDF-1.4 image-only"), "application/pdf")},
                   headers=ANALYST1)
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["status"] == "ready"
        assert doc["extraction"] == "ocr"


def test_scanned_pdf_stays_no_text_when_ocr_disabled(monkeypatch):
    monkeypatch.setattr(vaf, "classify_document", lambda filename, text: None)
    monkeypatch.setattr(vaf, "extract_text", lambda content, ext, max_chars=0: "")
    monkeypatch.setattr(ocr.settings, "ocr_enabled", False)
    with TestClient(doc_main.app) as c:
        case = _make_case(c)
        r = c.post(f"/api/cases/{case['id']}/documents",
                   files={"file": ("scan.pdf", io.BytesIO(b"%PDF-1.4 image-only"), "application/pdf")},
                   headers=ANALYST1)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "no_text"
