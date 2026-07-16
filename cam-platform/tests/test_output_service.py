"""Output/editor service tests: CAM lifecycle, versioning/autosave, optimistic
locking, conversational suggestions (human-in-the-loop), finalisation, exports.

Cross-service calls (genai edit, document text) are monkeypatched — no other
service is running.
"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_service_headers, make_user_headers

from cam.services.output import main as output_main

WATERMARK_TEXT = "AI-ASSISTED DRAFT"


@pytest.fixture
def client():
    with TestClient(output_main.app) as c:
        yield c


@pytest.fixture
def fake_genai(monkeypatch):
    """Capture genai payloads and return a deterministic edit proposal."""
    calls: list[dict] = []

    def _edit(payload: dict) -> dict:
        calls.append(payload)
        return {
            "proposed_content": payload["current_content"] + "\n\nRevised per instruction.",
            "rationale": "Tightened the wording as instructed.",
            "model": "mock", "usage": {"input_tokens": 10, "output_tokens": 5},
        }

    monkeypatch.setattr(output_main, "genai_edit", _edit)
    monkeypatch.setattr(output_main, "fetch_document_text", lambda doc_id: f"text of {doc_id}")
    return calls


def create_cam(client, created_by: str = "analyst1", case_id: str | None = None) -> dict:
    body = {
        "case_id": case_id or str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "title": "CAM — Acme Industries Ltd",
        "template_key": "corporate_etb",
        "created_by": created_by,
        "sections": [
            {"section_code": "exec_summary", "name": "Executive Summary", "order": 1,
             "content": "Acme is a **leading** manufacturer.\n\n- Strong order book\n- Diversified clients",
             "fixed_format": False, "generated": True},
            {"section_code": "financials", "name": "Financial Analysis", "order": 2,
             "content": "### Key metrics\n\n| Metric | FY25 |\n|---|---|\n| Revenue | ₹1,200 Cr |\n| EBITDA | ₹180 Cr |",
             "fixed_format": True, "generated": True},
            {"section_code": "_gaps", "name": "Data Gaps", "order": 99,
             "content": "- Missing: sanction letter", "fixed_format": True, "generated": True},
        ],
    }
    resp = client.post("/api/cams", json=body, headers=make_service_headers("orchestration"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def section_by_code(cam: dict, code: str) -> dict:
    return next(s for s in cam["sections"] if s["section_code"] == code)


ANALYST1 = make_user_headers("analyst1", ["analyst"])
ANALYST2 = make_user_headers("analyst2", ["analyst"])
REVIEWER = make_user_headers("reviewer1", ["reviewer"])
AUDITOR = make_user_headers("auditor1", ["auditor"])


class TestCreateAndScoping:
    def test_service_create_returns_full_cam(self, client, captured_audit):
        cam = create_cam(client)
        assert cam["status"] == "draft"
        assert [s["section_code"] for s in cam["sections"]] == ["exec_summary", "financials", "_gaps"]
        exec_s = section_by_code(cam, "exec_summary")
        assert exec_s["current_version_no"] == 1
        assert "leading" in exec_s["content"]
        created = [e for e in captured_audit if e["action"] == "cam.created"]
        assert created and created[0]["detail"]["section_codes"] == ["exec_summary", "financials", "_gaps"]

    def test_user_cannot_create_cam(self, client):
        resp = client.post("/api/cams", json={}, headers=ANALYST1)
        assert resp.status_code == 403

    def test_analyst_own_scoping(self, client):
        cam = create_cam(client, created_by="analyst1")
        # creator sees it
        assert client.get(f"/api/cams/{cam['id']}", headers=ANALYST1).status_code == 200
        listed = client.get(f"/api/cams?case_id={cam['case_id']}", headers=ANALYST1).json()
        assert [c["id"] for c in listed] == [cam["id"]]
        # another analyst does not
        assert client.get(f"/api/cams/{cam['id']}", headers=ANALYST2).status_code == 404
        assert client.get(f"/api/cams?case_id={cam['case_id']}", headers=ANALYST2).json() == []
        # reviewer and auditor see all
        assert client.get(f"/api/cams/{cam['id']}", headers=REVIEWER).status_code == 200
        assert client.get(f"/api/cams/{cam['id']}", headers=AUDITOR).status_code == 200

    def test_first_version_source_generated(self, client):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        versions = client.get(f"/api/cams/{cam['id']}/sections/{sec['id']}/versions",
                              headers=ANALYST1).json()
        assert len(versions) == 1
        assert versions[0]["source"] == "generated"
        assert versions[0]["created_by"] == "analyst1"


class TestEditingAndVersions:
    def test_edit_appends_version_over_generated_head(self, client, captured_audit):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        resp = client.put(f"/api/cams/{cam['id']}/sections/{sec['id']}",
                          json={"content": "Edited body.", "base_version_no": 1},
                          headers=ANALYST1)
        assert resp.status_code == 200, resp.text
        version = resp.json()
        assert version["version_no"] == 2
        assert version["source"] == "manual"
        assert version["name"] is None
        assert version["content"] == "Edited body."
        got = client.get(f"/api/cams/{cam['id']}", headers=ANALYST1).json()
        assert section_by_code(got, "exec_summary")["content"] == "Edited body."
        edited = [e for e in captured_audit if e["action"] == "cam.section_edited"]
        assert edited[-1]["detail"] == {"section_code": "exec_summary", "version_no": 2, "named": False}

    def test_autosave_updates_in_place_then_named_save_appends(self, client):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        url = f"/api/cams/{cam['id']}/sections/{sec['id']}"
        # first unnamed save appends v2 (head is 'generated')
        v2 = client.put(url, json={"content": "draft one", "base_version_no": 1},
                        headers=ANALYST1).json()
        assert v2["version_no"] == 2
        # second unnamed save by the same user coalesces into v2
        again = client.put(url, json={"content": "draft two", "base_version_no": 2},
                           headers=ANALYST1)
        assert again.status_code == 200
        assert again.json()["version_no"] == 2
        assert again.json()["content"] == "draft two"
        versions = client.get(f"{url}/versions", headers=ANALYST1).json()
        assert [v["version_no"] for v in versions] == [2, 1]  # newest first
        v2_full = client.get(f"{url}/versions/2", headers=ANALYST1).json()
        assert v2_full["content"] == "draft two"
        # a named save appends a fresh version
        named = client.put(url, json={"content": "checkpoint", "version_name": "pre-review",
                                      "base_version_no": 2}, headers=ANALYST1).json()
        assert named["version_no"] == 3
        assert named["name"] == "pre-review"
        versions = client.get(f"{url}/versions", headers=ANALYST1).json()
        assert [v["version_no"] for v in versions] == [3, 2, 1]

    def test_stale_base_version_conflicts(self, client):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        url = f"/api/cams/{cam['id']}/sections/{sec['id']}"
        client.put(url, json={"content": "newer", "base_version_no": 1}, headers=ANALYST1)
        resp = client.put(url, json={"content": "stale write", "base_version_no": 1},
                          headers=ANALYST1)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "conflict"

    def test_gap_trailer_never_editable(self, client):
        cam = create_cam(client)
        gaps = section_by_code(cam, "_gaps")
        resp = client.put(f"/api/cams/{cam['id']}/sections/{gaps['id']}",
                          json={"content": "sneaky", "base_version_no": 1}, headers=ANALYST1)
        assert resp.status_code == 422

    def test_diff_endpoint(self, client):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        url = f"/api/cams/{cam['id']}/sections/{sec['id']}"
        client.put(url, json={"content": "New body line.", "base_version_no": 1},
                   headers=ANALYST1)
        diff = client.get(f"{url}/diff?from=1&to=2", headers=ANALYST1).json()["diff"]
        assert "--- v1" in diff and "+++ v2" in diff
        assert any(line.startswith("-") for line in diff.splitlines()[2:])
        assert "+New body line." in diff

    def test_regeneration_version_service_only(self, client, captured_audit):
        cam = create_cam(client)
        sec = section_by_code(cam, "financials")
        url = f"/api/cams/{cam['id']}/sections/{sec['id']}/versions"
        # user tokens may not post regenerated content
        assert client.post(url, json={"content": "x", "source": "regeneration"},
                           headers=ANALYST1).status_code == 403
        resp = client.post(url, json={"content": "Regenerated table.", "source": "regeneration"},
                           headers=make_service_headers("orchestration"))
        assert resp.status_code == 201, resp.text
        version = resp.json()
        assert version["version_no"] == 2
        assert version["source"] == "regeneration"
        assert version["created_by"] == "svc:orchestration"
        got = client.get(f"/api/cams/{cam['id']}", headers=ANALYST1).json()
        assert section_by_code(got, "financials")["content"] == "Regenerated table."
        edited = [e for e in captured_audit if e["action"] == "cam.section_edited"]
        assert edited[-1]["detail"]["source"] == "regeneration"


class TestChatAndSuggestions:
    def test_section_chat_creates_pending_suggestion(self, client, fake_genai, captured_audit):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        resp = client.post(f"/api/cams/{cam['id']}/chat",
                           json={"scope": "section", "section_id": sec["id"],
                                 "message": "Make it crisper",
                                 "attached_document_ids": ["doc-77"]},
                           headers=ANALYST1)
        assert resp.status_code == 200, resp.text
        out = resp.json()
        # genai payload per contracts.md §6 /edit
        payload = fake_genai[0]
        assert payload["instruction"] == "Make it crisper"
        assert payload["scope"] == "section"
        assert payload["current_content"] == sec["content"]
        assert payload["preferences"] is None
        assert payload["grounding_docs"] == [
            {"doctype_code": "chat_attachment", "label": "doc-77", "text": "text of doc-77"}]
        # transcript persisted
        assert out["message"]["role"] == "user"
        assert out["message"]["attached_document_ids"] == ["doc-77"]
        assert out["reply"]["role"] == "assistant"
        assert out["reply"]["content"] == "Tightened the wording as instructed."
        history = client.get(f"/api/cams/{cam['id']}/chat?section_id={sec['id']}",
                             headers=ANALYST1).json()
        assert [m["role"] for m in history] == ["user", "assistant"]
        # suggestion pending with a real diff; document untouched (FR-E06)
        sug = out["suggestion"]
        assert sug["status"] == "pending"
        assert "+Revised per instruction." in sug["diff"]
        got = client.get(f"/api/cams/{cam['id']}", headers=ANALYST1).json()
        assert section_by_code(got, "exec_summary")["content"] == sec["content"]
        pending = client.get(f"/api/cams/{cam['id']}/suggestions?status=pending",
                             headers=ANALYST1).json()
        assert [s["id"] for s in pending] == [sug["id"]]
        actions = [e["action"] for e in captured_audit]
        assert "cam.chat_message" in actions and "cam.suggestion_created" in actions

    def test_accept_applies_as_new_version(self, client, fake_genai):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        sug = client.post(f"/api/cams/{cam['id']}/chat",
                          json={"scope": "section", "section_id": sec["id"],
                                "message": "Sharpen"},
                          headers=ANALYST1).json()["suggestion"]
        resp = client.post(f"/api/cams/{cam['id']}/suggestions/{sug['id']}/accept",
                           headers=ANALYST1)
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["suggestion"]["status"] == "accepted"
        assert out["suggestion"]["decided_by"] == "analyst1"
        assert out["new_version"]["version_no"] == 2
        assert out["new_version"]["source"] == "chat_suggestion"
        got = client.get(f"/api/cams/{cam['id']}", headers=ANALYST1).json()
        assert section_by_code(got, "exec_summary")["content"] == sug["proposed_content"]
        # cannot double-decide
        assert client.post(f"/api/cams/{cam['id']}/suggestions/{sug['id']}/accept",
                           headers=ANALYST1).status_code == 409

    def test_reject_leaves_content_untouched(self, client, fake_genai):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        sug = client.post(f"/api/cams/{cam['id']}/chat",
                          json={"scope": "section", "section_id": sec["id"],
                                "message": "Rewrite"},
                          headers=ANALYST1).json()["suggestion"]
        resp = client.post(f"/api/cams/{cam['id']}/suggestions/{sug['id']}/reject",
                           json={"reason": "hallucinated figures"}, headers=ANALYST1)
        assert resp.status_code == 200
        assert resp.json()["suggestion"]["status"] == "rejected"
        got = client.get(f"/api/cams/{cam['id']}", headers=ANALYST1).json()
        assert section_by_code(got, "exec_summary")["content"] == sec["content"]
        rejected = client.get(f"/api/cams/{cam['id']}/suggestions?status=rejected",
                              headers=ANALYST1).json()
        assert [s["id"] for s in rejected] == [sug["id"]]

    def test_document_scope_chat_is_advisory_only(self, client, fake_genai):
        cam = create_cam(client)
        resp = client.post(f"/api/cams/{cam['id']}/chat",
                           json={"scope": "document", "message": "What risks stand out?"},
                           headers=ANALYST1)
        assert resp.status_code == 200
        out = resp.json()
        assert out["suggestion"] is None
        assert out["reply"]["role"] == "assistant"
        # whole document (sections joined with markdown headers) went to genai
        payload = fake_genai[0]
        assert "## Executive Summary" in payload["current_content"]
        assert "## Financial Analysis" in payload["current_content"]
        assert client.get(f"/api/cams/{cam['id']}/suggestions?status=pending",
                          headers=ANALYST1).json() == []


class TestFinaliseAndExport:
    def test_finalise_blocked_by_pending_suggestion_then_locks_cam(
            self, client, fake_genai, captured_audit):
        cam = create_cam(client)
        sec = section_by_code(cam, "exec_summary")
        sug = client.post(f"/api/cams/{cam['id']}/chat",
                          json={"scope": "section", "section_id": sec["id"],
                                "message": "Polish"},
                          headers=ANALYST1).json()["suggestion"]
        blocked = client.post(f"/api/cams/{cam['id']}/finalise", headers=ANALYST1)
        assert blocked.status_code == 409
        client.post(f"/api/cams/{cam['id']}/suggestions/{sug['id']}/reject",
                    json={"reason": "not needed"}, headers=ANALYST1)
        done = client.post(f"/api/cams/{cam['id']}/finalise", headers=ANALYST1)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "final"
        assert done.json()["finalised_by"] == "analyst1"
        assert done.json()["finalised_at"]
        assert any(e["action"] == "cam.finalised" for e in captured_audit)
        # already final
        assert client.post(f"/api/cams/{cam['id']}/finalise",
                           headers=ANALYST1).status_code == 409
        # editing and chat are locked
        edit = client.put(f"/api/cams/{cam['id']}/sections/{sec['id']}",
                          json={"content": "late edit", "base_version_no": 2},
                          headers=ANALYST1)
        assert edit.status_code == 409
        chat = client.post(f"/api/cams/{cam['id']}/chat",
                           json={"scope": "document", "message": "hi"}, headers=ANALYST1)
        assert chat.status_code == 409

    def test_docx_export_with_watermark_lifecycle(self, client, captured_audit):
        cam = create_cam(client)
        resp = client.get(f"/api/cams/{cam['id']}/export.docx", headers=ANALYST1)
        assert resp.status_code == 200
        assert resp.content[:4] == b"PK\x03\x04"  # zip magic
        assert f"CAM_{cam['id'][:8]}.docx" in resp.headers["content-disposition"]
        assert "attachment" in resp.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            document_xml = zf.read("word/document.xml").decode("utf-8")
            header_xml = "".join(zf.read(n).decode("utf-8") for n in zf.namelist()
                                 if n.startswith("word/header"))
        assert WATERMARK_TEXT in document_xml  # draft banner (FR-E08)
        assert WATERMARK_TEXT in header_xml  # page-header watermark
        assert "Executive Summary" in document_xml
        assert "Disclosed data gaps" in document_xml  # gap trailer always rendered
        assert any(e["action"] == "cam.exported" and e["detail"] == {"format": "docx"}
                   for e in captured_audit)
        # finalise -> watermark drops from a fresh export
        assert client.post(f"/api/cams/{cam['id']}/finalise",
                           headers=ANALYST1).status_code == 200
        final = client.get(f"/api/cams/{cam['id']}/export.docx", headers=ANALYST1)
        with zipfile.ZipFile(io.BytesIO(final.content)) as zf:
            final_xml = zf.read("word/document.xml").decode("utf-8")
            final_headers = "".join(zf.read(n).decode("utf-8") for n in zf.namelist()
                                    if n.startswith("word/header"))
        assert WATERMARK_TEXT not in final_xml
        assert WATERMARK_TEXT not in final_headers

    def test_pdf_export(self, client, captured_audit):
        cam = create_cam(client)
        resp = client.get(f"/api/cams/{cam['id']}/export.pdf", headers=ANALYST1)
        assert resp.status_code == 200
        assert resp.content[:5] == b"%PDF-"
        assert f"CAM_{cam['id'][:8]}.pdf" in resp.headers["content-disposition"]
        assert any(e["action"] == "cam.exported" and e["detail"] == {"format": "pdf"}
                   for e in captured_audit)

    def test_export_requires_download_capability(self, client):
        cam = create_cam(client)
        # auditor has no cam:download
        assert client.get(f"/api/cams/{cam['id']}/export.pdf",
                          headers=AUDITOR).status_code == 403
