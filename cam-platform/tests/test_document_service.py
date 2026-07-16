"""document service tests — cases + own-scoping, VAF intake (happy path,
quarantine, duplicates, rejections), tags, completeness, extraction."""
from __future__ import annotations

import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_service_headers, make_user_headers

from cam.services.document import main as doc_main
from cam.services.document import vaf

ANALYST1 = make_user_headers("analyst1", ["analyst"])
ANALYST2 = make_user_headers("analyst2", ["analyst"])
REVIEWER = make_user_headers("reviewer1", ["reviewer"])
AUDITOR = make_user_headers("auditor1", ["auditor"])
ADMIN = make_user_headers("admin1", ["business_admin"])
SERVICE = make_service_headers("orchestration")

CLASSIFY_FINANCIALS = {
    "candidates": [{"doctype_code": "financials", "confidence": 0.75}],
    "threshold": 0.55,
    "best": {"doctype_code": "financials", "confidence": 0.75, "needs_review": False},
}


@pytest.fixture()
def client():
    with TestClient(doc_main.app) as c:
        yield c


@pytest.fixture()
def no_auto_tag(monkeypatch):
    """Tagging service unavailable — intake must proceed untagged."""
    monkeypatch.setattr(vaf, "classify_document", lambda filename, text: None)


@pytest.fixture()
def auto_tag_financials(monkeypatch):
    monkeypatch.setattr(vaf, "classify_document",
                        lambda filename, text: dict(CLASSIFY_FINANCIALS))


def make_case(client, headers=ANALYST1, borrower="Acme Industries Ltd"):
    resp = client.post("/api/cases", json={
        "borrower_name": borrower, "segment": "corporate",
        "relationship": "etb", "industry_code": "IND-STEEL"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def upload(client, case_id, filename, content, headers=ANALYST1,
           content_type="text/plain", **form):
    data = {k: v for k, v in form.items() if v is not None}
    return client.post(f"/api/cases/{case_id}/documents",
                       files={"file": (filename, io.BytesIO(content), content_type)},
                       data=data, headers=headers)


# ------------------------------------------------------------------ cases

def test_create_case_shape_and_audit(client, captured_audit):
    case = make_case(client)
    assert case["borrower_name"] == "Acme Industries Ltd"
    assert case["segment"] == "corporate"
    assert case["relationship"] == "etb"
    assert case["industry_code"] == "IND-STEEL"
    assert case["status"] == "open"
    assert case["created_by"] == "analyst1"
    assert case["created_at"].endswith("Z")
    events = [e for e in captured_audit if e["action"] == "case.created"]
    assert events and events[0]["case_id"] == case["id"]
    assert events[0]["actor"] == "analyst1"


def test_case_own_scoping(client):
    case = make_case(client)

    # analyst2 cannot see analyst1's case
    assert client.get(f"/api/cases/{case['id']}", headers=ANALYST2).status_code == 403
    assert case["id"] not in {c["id"] for c in
                              client.get("/api/cases", headers=ANALYST2).json()}
    # owner, reviewer, auditor and service tokens can
    for headers in (ANALYST1, REVIEWER, AUDITOR, SERVICE):
        assert client.get(f"/api/cases/{case['id']}", headers=headers).status_code == 200
        assert case["id"] in {c["id"] for c in
                              client.get("/api/cases", headers=headers).json()}
    # business_admin has no case:read; unknown id is 404; no token is 401
    assert client.get(f"/api/cases/{case['id']}", headers=ADMIN).status_code == 403
    assert client.get("/api/cases/nope", headers=ANALYST1).status_code == 404
    assert client.get("/api/cases").status_code == 401


def test_case_create_requires_analyst(client):
    resp = client.post("/api/cases", json={
        "borrower_name": "X", "segment": "corporate",
        "relationship": "ntb", "industry_code": "I"}, headers=REVIEWER)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# ----------------------------------------------------------------- intake

def test_upload_txt_happy_path(client, captured_audit, auto_tag_financials):
    case = make_case(client)
    content = b"Annual report FY2025 with balance sheet and P&L."
    resp = upload(client, case["id"], "annual_report_fy25.txt", content,
                  period_label="FY2025")
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["status"] == "ready"
    assert doc["extraction"] == "ok"
    assert doc["sha256"] == hashlib.sha256(content).hexdigest()
    assert doc["size_bytes"] == len(content)
    assert doc["origin"] == "upload"
    assert doc["duplicate_of"] is None
    assert doc["quarantine_reason"] is None
    assert doc["uploaded_by"] == "analyst1"

    # mocked classify result applied as an auto tag, period_label lands on it
    assert len(doc["tags"]) == 1
    tag = doc["tags"][0]
    assert tag["source"] == "auto"
    assert tag["doctype_code"] == "financials"
    assert tag["confidence"] == 0.75
    assert tag["needs_review"] is False
    assert tag["period_label"] == "FY2025"

    # text endpoint: owner and service tokens both read the extract
    for headers in (ANALYST1, SERVICE):
        text_resp = client.get(f"/api/documents/{doc['id']}/text", headers=headers)
        assert text_resp.status_code == 200
        assert text_resp.json()["text"] == content.decode()

    uploaded = [e for e in captured_audit if e["action"] == "document.uploaded"]
    assert uploaded and uploaded[-1]["entity_id"] == doc["id"]
    assert uploaded[-1]["case_id"] == case["id"]
    assert uploaded[-1]["detail"]["sha256"] == doc["sha256"]
    assert uploaded[-1]["detail"]["filename"] == "annual_report_fy25.txt"
    assert uploaded[-1]["detail"]["size_bytes"] == len(content)
    assert uploaded[-1]["detail"]["doctype"] == "financials"
    auto = [e for e in captured_audit if e["action"] == "tag.auto_applied"]
    assert auto and auto[-1]["entity_id"] == tag["id"]


def test_upload_rejects_multiple_files(client, no_auto_tag):
    case = make_case(client)
    resp = client.post(
        f"/api/cases/{case['id']}/documents",
        files=[("file", ("a.txt", io.BytesIO(b"one"), "text/plain")),
               ("file", ("b.txt", io.BytesIO(b"two"), "text/plain"))],
        headers=ANALYST1)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert client.get(f"/api/cases/{case['id']}/documents",
                      headers=ANALYST1).json() == []


def test_eicar_quarantine(client, captured_audit, monkeypatch):
    def never(filename, text):  # quarantined files must not reach tagging
        raise AssertionError("classify_document called for a quarantined file")
    monkeypatch.setattr(vaf, "classify_document", never)

    case = make_case(client)
    resp = upload(client, case["id"], "invoice.txt", b"prefix " + vaf.EICAR_SIGNATURE)
    assert resp.status_code == 201  # record persisted so the user sees why
    doc = resp.json()
    assert doc["status"] == "quarantined"
    assert doc["quarantine_reason"]
    assert doc["tags"] == []
    # content is never stored
    assert not (doc_main.settings.blob_dir / f"{doc['id']}.txt").exists()
    assert not (doc_main.settings.extract_dir / f"{doc['id']}.txt").exists()

    text_resp = client.get(f"/api/documents/{doc['id']}/text", headers=ANALYST1)
    assert text_resp.status_code == 409
    assert text_resp.json()["error"]["code"] == "quarantined"

    actions = {e["action"] for e in captured_audit if e["entity_id"] == doc["id"]}
    assert actions == {"document.quarantined"}


def test_duplicate_detection_warns_and_proceeds(client, no_auto_tag):
    case = make_case(client)
    content = b"identical stock statement content"
    first = upload(client, case["id"], "stock_jan.txt", content).json()
    assert first["duplicate_of"] is None
    second = upload(client, case["id"], "stock_jan_copy.txt", content).json()
    assert second["duplicate_of"] == first["id"]
    assert second["status"] == "ready"  # warn-and-proceed (FR-C07)
    # a different case is unaffected
    other = make_case(client, borrower="Other Co")
    assert upload(client, other["id"], "stock_jan.txt", content).json()["duplicate_of"] is None


def test_extension_size_and_empty_rejections(client, no_auto_tag, monkeypatch):
    case = make_case(client)

    exe = upload(client, case["id"], "payload.exe", b"MZ...")
    assert exe.status_code == 201
    assert exe.json()["status"] == "quarantined"
    assert "not allowed" in exe.json()["quarantine_reason"]

    empty = upload(client, case["id"], "empty.txt", b"").json()
    assert empty["status"] == "quarantined"
    assert "empty" in empty["quarantine_reason"]

    monkeypatch.setattr(vaf.settings, "max_upload_mb", 0)
    big = upload(client, case["id"], "big.txt", b"x").json()
    assert big["status"] == "quarantined"
    assert "exceeds" in big["quarantine_reason"]


def test_whitespace_only_text_is_no_text(client, no_auto_tag):
    case = make_case(client)
    doc = upload(client, case["id"], "blank.txt", b"   \n\t  ").json()
    assert doc["status"] == "no_text"
    assert doc["extraction"] == "empty"


def test_pdf_extraction_with_real_pdf(client, no_auto_tag):
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(text="Audited balance sheet FY2025 for Acme Industries")
    content = bytes(pdf.output())

    case = make_case(client)
    resp = upload(client, case["id"], "audited_financials.pdf", content,
                  content_type="application/pdf")
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["status"] == "ready"
    assert doc["extraction"] == "ok"
    text = client.get(f"/api/documents/{doc['id']}/text", headers=ANALYST1).json()["text"]
    assert "Audited balance sheet FY2025" in text


def test_pull_from_repository(client, captured_audit, auto_tag_financials):
    import pathlib
    repo = pathlib.Path(doc_main.settings.data_dir) / "repository"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "audited_fin_2025.txt").write_bytes(b"Audited financials pulled from DMS.")

    case = make_case(client)
    resp = client.post(f"/api/cases/{case['id']}/pull",
                       json={"source": "repository", "external_ref": "audited_fin_2025.txt"},
                       headers=ANALYST1)
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["origin"] == "repository"
    assert doc["status"] == "ready"
    assert doc["tags"][0]["source"] == "auto"  # same pipeline incl. auto-tag

    pulled = [e for e in captured_audit if e["action"] == "document.pulled"]
    assert pulled and pulled[-1]["detail"]["external_ref"] == "audited_fin_2025.txt"
    assert pulled[-1]["detail"]["sha256"] == doc["sha256"]

    missing = client.post(f"/api/cases/{case['id']}/pull",
                          json={"source": "repository", "external_ref": "nope.txt"},
                          headers=ANALYST1)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"

    bad_source = client.post(f"/api/cases/{case['id']}/pull",
                             json={"source": "sharepoint", "external_ref": "x.txt"},
                             headers=ANALYST1)
    assert bad_source.status_code == 422


def test_upload_permissions(client, no_auto_tag):
    case = make_case(client)
    # reviewer lacks docs:manage
    assert upload(client, case["id"], "a.txt", b"x", headers=REVIEWER).status_code == 403
    # analyst2 cannot upload into analyst1's case
    assert upload(client, case["id"], "a.txt", b"x", headers=ANALYST2).status_code == 403


def test_delete_document_removes_files_and_audits(client, captured_audit, no_auto_tag):
    case = make_case(client)
    doc = upload(client, case["id"], "temp.txt", b"to be deleted").json()
    blob = doc_main.settings.blob_dir / f"{doc['id']}.txt"
    extract = doc_main.settings.extract_dir / f"{doc['id']}.txt"
    assert blob.exists() and extract.exists()

    resp = client.delete(f"/api/documents/{doc['id']}", headers=ANALYST1)
    assert resp.status_code == 204
    assert not blob.exists() and not extract.exists()
    assert client.get(f"/api/documents/{doc['id']}", headers=ANALYST1).status_code == 404

    deleted = [e for e in captured_audit if e["action"] == "document.deleted"]
    assert deleted and deleted[-1]["entity_id"] == doc["id"]
    assert deleted[-1]["detail"]["sha256"] == doc["sha256"]


# ------------------------------------------------------------------- tags

def test_tag_crud_with_audit(client, captured_audit, no_auto_tag):
    case = make_case(client)
    doc = upload(client, case["id"], "kyc_pack.txt", b"KYC documents").json()
    assert doc["tags"] == []  # classify returned None -> no auto tag

    created = client.post(f"/api/documents/{doc['id']}/tags",
                          json={"doctype_code": "kyc", "period_label": "FY2024",
                                "seq_order": 1},
                          headers=ANALYST1)
    assert created.status_code == 201, created.text
    tag = created.json()
    assert tag["source"] == "user"
    assert tag["needs_review"] is False
    assert tag["confidence"] is None
    assert tag["period_label"] == "FY2024"
    assert tag["seq_order"] == 1
    assert tag["page_range"] is None
    added = [e for e in captured_audit if e["action"] == "tag.added"]
    assert added and added[-1]["detail"]["after"]["doctype_code"] == "kyc"
    assert added[-1]["case_id"] == case["id"]

    patched = client.patch(f"/api/documents/{doc['id']}/tags/{tag['id']}",
                           json={"doctype_code": "financials", "seq_order": 2},
                           headers=ANALYST1)
    assert patched.status_code == 200
    assert patched.json()["doctype_code"] == "financials"
    assert patched.json()["seq_order"] == 2
    changed = [e for e in captured_audit if e["action"] == "tag.changed"]
    assert changed[-1]["detail"]["before"]["doctype_code"] == "kyc"
    assert changed[-1]["detail"]["after"]["doctype_code"] == "financials"

    # tag now visible on the document
    doc_now = client.get(f"/api/documents/{doc['id']}", headers=ANALYST1).json()
    assert [t["doctype_code"] for t in doc_now["tags"]] == ["financials"]

    deleted = client.delete(f"/api/documents/{doc['id']}/tags/{tag['id']}",
                            headers=ANALYST1)
    assert deleted.status_code == 204
    removed = [e for e in captured_audit if e["action"] == "tag.removed"]
    assert removed and removed[-1]["detail"]["before"]["id"] == tag["id"]
    assert client.get(f"/api/documents/{doc['id']}",
                      headers=ANALYST1).json()["tags"] == []

    assert client.patch(f"/api/documents/{doc['id']}/tags/{tag['id']}",
                        json={"confirmed": True}, headers=ANALYST1).status_code == 404


def test_confirm_auto_tag_clears_needs_review_keeps_source(client, monkeypatch):
    low_conf = {"candidates": [{"doctype_code": "financials", "confidence": 0.3}],
                "threshold": 0.55,
                "best": {"doctype_code": "financials", "confidence": 0.3,
                         "needs_review": True}}
    monkeypatch.setattr(vaf, "classify_document", lambda filename, text: low_conf)
    case = make_case(client)
    doc = upload(client, case["id"], "maybe_financials.txt", b"some text").json()
    tag = doc["tags"][0]
    assert tag["needs_review"] is True and tag["source"] == "auto"

    confirmed = client.patch(f"/api/documents/{doc['id']}/tags/{tag['id']}",
                             json={"confirmed": True}, headers=ANALYST1).json()
    assert confirmed["needs_review"] is False
    assert confirmed["source"] == "auto"  # confirmation does not rewrite provenance


# ----------------------------------------------------------- completeness

def test_completeness_with_mocked_template(client, monkeypatch, auto_tag_financials):
    def fake_resolve(template_key):
        assert template_key == "corp-standard"
        return {"template_key": template_key,
                "template": {"required_doc_types": ["financials", "kyc", "projections"]}}
    monkeypatch.setattr(doc_main, "fetch_resolved_template", fake_resolve)

    case = make_case(client)
    doc = upload(client, case["id"], "annual_report.txt", b"financial text").json()
    assert doc["tags"][0]["doctype_code"] == "financials"  # auto
    client.post(f"/api/documents/{doc['id']}/tags", json={"doctype_code": "kyc"},
                headers=ANALYST1)

    # a tag on a quarantined document must NOT count as present
    quarantined = upload(client, case["id"], "bad.txt",
                         vaf.EICAR_SIGNATURE).json()
    client.post(f"/api/documents/{quarantined['id']}/tags",
                json={"doctype_code": "projections"}, headers=ANALYST1)

    resp = client.get(f"/api/cases/{case['id']}/completeness",
                      params={"template_key": "corp-standard"}, headers=ANALYST1)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["required"] == ["financials", "kyc", "projections"]
    assert body["present"] == ["financials", "kyc"]
    assert body["missing"] == ["projections"]
    assert body["can_proceed"] is True
