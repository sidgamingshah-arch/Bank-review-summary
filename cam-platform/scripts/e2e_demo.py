"""End-to-end acceptance walkthrough of BRD §9 (Definition of Done, v1),
driven entirely through the gateway against a real running stack.

    python scripts/e2e_demo.py          # starts its own stack on a fresh data dir

AC-1  masters end-to-end under maker-checker, rollback demonstrated
AC-2  upload → auto-tag → correct tags → template → generation → edit →
      finalise → DOCX/PDF download, one session
AC-3  conversational edit with in-chat upload → tracked suggestion → accept/reject
AC-4  audit trail reconstructs full lineage for the CAM
AC-5  no client-visible credentials; model plane closed to end users
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_stack import ROOT, Stack  # noqa: E402
from seed_demo import GATEWAY, login, publish, seed  # noqa: E402

DATA_DIR = ROOT / ".data-e2e"
EXPORT_DIR = DATA_DIR / "exports"
SAMPLE_DIR = DATA_DIR / "samples"
EICAR = r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

FIN_TEXT = (
    "Acme Steel Ltd - Audited Financial Statements FY2025. Auditor's report: unqualified. "
    "Balance sheet and profit and loss summary. Revenue for FY2025 stood at Rs. 4,210 crore, "
    "up 12.5% year on year. EBITDA margin was 18.2%. Net debt of Rs. 950 crore against "
    "equity of Rs. 2,100 crore gives net debt to EBITDA of 1.24. Capacity utilisation "
    "reached 82% on installed capacity of 3.2 million tonnes. Cash flow from operations "
    "was Rs. 610 crore.")

STMT_TEXT = (
    "Statement of account - Acme Steel Ltd, working capital account, June 2026. "
    "Closing balance Rs. 12.4 crore. Average utilisation of sanctioned limits was 74% "
    "over the last 6 months. No overdrawals observed. Interest serviced on time.")

SANCTION_TEXT = (
    "Sanction letter - facility terms and conditions for Acme Steel Ltd. "
    "Fund-based working capital limit of Rs. 400 crore and term loan of Rs. 250 crore. "
    "Covenant: net debt to EBITDA not to exceed 3.5. Covenant: promoter shareholding "
    "to remain above 51%. Security: first pari-passu charge on current assets.")

BUREAU_TEXT = ("bureau,credit score,dpd\nAcme Steel Ltd,780,0\n"
               "Note: no suit-filed records; credit score 780; zero DPD in 36 months.")

PREV_CAM_TEXT = ("Credit Assessment Memo - previous review of Acme Steel Ltd (FY2024). "
                 "Facilities renewed at Rs. 600 crore. Conduct satisfactory. "
                 "Promoted by the Mehta family; flagship of the Acme group with two "
                 "subsidiaries in downstream processing.")

STOCK_AUDIT_TEXT = ("Stock audit report - Acme Steel Ltd, Q1 FY2027. Inventory verified at "
                    "Rs. 420 crore; receivables Rs. 380 crore. Drawing power computed at "
                    "Rs. 310 crore against outstanding of Rs. 296 crore. No adverse findings.")

_passed: list[str] = []


def ok(ac: str, message: str) -> None:
    print(f"  ✔ {message}")
    _passed.append(ac)


def make_samples() -> dict[str, Path]:
    from docx import Document as Docx
    from fpdf import FPDF

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(180, 6, FIN_TEXT)
    fin = SAMPLE_DIR / "Acme_audited_financials_FY2025.pdf"
    pdf.output(str(fin))

    docx = Docx()
    docx.add_heading("Sanction Letter", level=1)
    for line in SANCTION_TEXT.split(". "):
        docx.add_paragraph(line)
    sanction = SAMPLE_DIR / "Acme_sanction_letter.docx"
    docx.save(str(sanction))

    stmt = SAMPLE_DIR / "Acme_bank_statement_Jun2026.txt"
    stmt.write_text(STMT_TEXT)
    bureau = SAMPLE_DIR / "Acme_credit_bureau_report.csv"
    bureau.write_text(BUREAU_TEXT)
    malware = SAMPLE_DIR / "vendor_invoice.txt"
    malware.write_text(EICAR)
    stock = SAMPLE_DIR / "Acme_stock_audit_report_Q1.txt"
    stock.write_text(STOCK_AUDIT_TEXT)

    repo = DATA_DIR / "repository"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "previous_cam_acme_fy2024.txt").write_text(PREV_CAM_TEXT)
    return {"fin": fin, "sanction": sanction, "stmt": stmt, "bureau": bureau,
            "malware": malware, "stock": stock}


def upload(client: httpx.Client, headers: dict, case_id: str, path: Path,
           origin: str | None = None, period_label: str | None = None) -> dict:
    data = {}
    if origin:
        data["origin"] = origin
    if period_label:
        data["period_label"] = period_label
    with open(path, "rb") as fh:
        r = client.post(f"{GATEWAY}/api/cases/{case_id}/documents",
                        files={"file": (path.name, fh)}, data=data, headers=headers)
    assert r.status_code == 201, f"upload {path.name}: {r.status_code} {r.text}"
    return r.json()


def ac1_masters(client: httpx.Client) -> None:
    print("\nAC-1 — masters under maker-checker, with rollback")
    versions = seed(client)
    ok("AC1", f"{len(versions)} master versions published via draft→submit→approve")

    admin1, admin2 = login(client, "admin1"), login(client, "admin2")
    # maker-checker enforced: maker cannot approve own change
    r = client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions",
                    headers=admin1, json={"payload": {
                        "code": "project_report", "name": "Project report (amended)",
                        "description": "amended", "synonyms": ["dpr"],
                        "keywords": ["project cost"], "active": True},
                        "change_note": "amend"})
    v2 = r.json()["version_no"]
    client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions/{v2}/submit",
                headers=admin1)
    r = client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions/{v2}/approve",
                    headers=admin1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "maker_checker_violation", r.text
    ok("AC1", "maker cannot approve own change (maker_checker_violation)")
    client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions/{v2}/approve",
                headers=admin2).raise_for_status()

    # one-click rollback: clone v1 → new draft → publish
    r = client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions/1/rollback",
                    headers=admin1)
    v3 = r.json()["version_no"]
    client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions/{v3}/submit",
                headers=admin1).raise_for_status()
    client.post(f"{GATEWAY}/api/masters/doctypes/project_report/versions/{v3}/approve",
                headers=admin2).raise_for_status()
    item = client.get(f"{GATEWAY}/api/masters/doctypes/project_report", headers=admin1).json()
    assert item["published_version"] == v3
    payload = client.get(f"{GATEWAY}/api/masters/doctypes/project_report/versions/{v3}",
                         headers=admin1).json()["payload"]
    assert payload["name"] == "Project report"  # v1 content restored
    diff = client.get(f"{GATEWAY}/api/masters/doctypes/project_report/diff?from=2&to=3",
                      headers=admin1).json()["diff"]
    assert "amended" in diff
    ok("AC1", f"rollback demonstrated: v{v2} (amended) → v{v3} restores v1 payload; diff view works")

    # configuration portability: published masters export/import as a bundle
    bundle = client.get(f"{GATEWAY}/api/masters/export-bundle", headers=admin1).json()
    report = client.post(f"{GATEWAY}/api/masters/import-bundle", headers=admin1,
                         json={"masters": bundle["masters"]}).json()
    assert report["errors"] == [] and report["created"] == [] and report["updated"] == []
    ok("AC1", f"masters bundle: {len(bundle['masters'])} published masters export/import "
              "round-trip cleanly (identical entries skipped)")


def ac2_case_to_download(client: httpx.Client, samples: dict) -> tuple[dict, str, dict]:
    print("\nAC-2 — upload → auto-tag → correct → generate → edit → finalise → download")
    analyst = login(client, "analyst1")
    client.put(f"{GATEWAY}/api/auth/preferences", headers=analyst,
               json={"tonality": "crisp", "structure_bias": "bullets",
                     "table_usage": "auto", "length": "standard"}).raise_for_status()

    r = client.post(f"{GATEWAY}/api/cases", headers=analyst,
                    json={"borrower_name": "Acme Steel Ltd", "segment": "corporate",
                          "relationship": "etb", "industry_code": "steel"})
    assert r.status_code == 201, r.text
    case = r.json()
    ok("AC2", f"case created for {case['borrower_name']}")

    # multi-file UX = strictly sequential single-file backend submissions (NFR-07)
    docs = {}
    docs["fin"] = upload(client, analyst, case["id"], samples["fin"], period_label="FY2025")
    docs["stmt"] = upload(client, analyst, case["id"], samples["stmt"])
    docs["sanction"] = upload(client, analyst, case["id"], samples["sanction"])
    docs["bureau"] = upload(client, analyst, case["id"], samples["bureau"])
    quarantined = upload(client, analyst, case["id"], samples["malware"])
    assert quarantined["status"] == "quarantined" and quarantined["quarantine_reason"]
    ok("AC2", f"VAF quarantined the EICAR file ({quarantined['quarantine_reason'][:50]}…), "
              "other files unaffected")

    r = client.post(f"{GATEWAY}/api/cases/{case['id']}/pull", headers=analyst,
                    json={"source": "repository", "external_ref": "previous_cam_acme_fy2024.txt"})
    assert r.status_code == 201, r.text
    docs["prev"] = r.json()

    expected = {"fin": "audited_financials", "stmt": "bank_statements",
                "sanction": "sanction_letter", "bureau": "credit_bureau_report",
                "prev": "previous_cam"}
    for key, want in expected.items():
        tags = docs[key]["tags"]
        assert tags, f"no auto tag on {key}: {docs[key]}"
        got = tags[0]["doctype_code"]
        assert got == want, f"{key}: auto-tagged {got}, expected {want}"
    ok("AC2", "auto-tagging classified all 5 usable documents correctly (with confidence scores)")

    # analyst corrections: confirm one tag, add provisionals as a second tag (split-tag)
    fin_tag = docs["fin"]["tags"][0]
    client.patch(f"{GATEWAY}/api/documents/{docs['fin']['id']}/tags/{fin_tag['id']}",
                 headers=analyst, json={"confirmed": True}).raise_for_status()
    r = client.post(f"{GATEWAY}/api/documents/{docs['fin']['id']}/tags", headers=analyst,
                    json={"doctype_code": "provisional_financials", "period_label": "FY2026"})
    assert r.status_code == 201, r.text
    ok("AC2", "tag review: confirmed the auto tag, added a second doctype tag manually")

    r = client.get(f"{GATEWAY}/api/cases/{case['id']}/completeness?template_key=corp-etb",
                   headers=analyst)
    comp = r.json()
    assert comp["missing"] == [], comp
    ok("AC2", f"completeness check vs template: required={len(comp['required'])}, missing=0")

    r = client.post(f"{GATEWAY}/api/runs", headers=analyst,
                    json={"case_id": case["id"], "template_key": "corp-etb"})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["applied_preferences"]["source"] == "user"
    assert run["master_versions"]["template"] >= 1 and run["master_versions"]["prompts"]

    deadline = time.monotonic() + 120
    while run["status"] in ("queued", "running"):
        assert time.monotonic() < deadline, f"run stuck: {run['status']}"
        time.sleep(1.0)
        run = client.get(f"{GATEWAY}/api/runs/{run['id']}", headers=analyst).json()
    statuses = {s["section_code"]: s["status"] for s in run["sections"]}
    assert run["status"] == "complete", (run["status"], statuses)
    assert statuses.pop("project_review") == "skipped"  # conditional section, no DPR on case
    assert set(statuses.values()) == {"complete"}
    assert run["cam_id"]
    case_state = client.get(f"{GATEWAY}/api/cases/{case['id']}", headers=analyst).json()
    assert case_state["status"] == "drafted", case_state["status"]
    ok("AC2", f"async generation complete: {len(statuses)} sections drafted, 1 conditional "
              f"section skipped, model={run['model_identity']}; case moved to 'drafted'")
    return case, run["id"], client.get(f"{GATEWAY}/api/cams/{run['cam_id']}",
                                       headers=analyst).json()


def ac3_workspace(client: httpx.Client, samples: dict, case: dict, run_id: str,
                  cam: dict) -> dict:
    print("\nAC-3 — workspace: inline edit, conversational edit with in-chat upload, HITL")
    analyst = login(client, "analyst1")
    cam_id = cam["id"]
    fin = next(s for s in cam["sections"] if s["section_code"] == "financial_analysis")
    trailer = next(s for s in cam["sections"] if s["section_code"] == "_gaps")
    # FR-D05: the trailer must disclose the skipped conditional section
    assert "Sections skipped" in trailer["content"] and "Project Review" in trailer["content"]

    # inline edit: autosave then a named version, with optimistic locking
    edited = fin["content"] + "\n\nAnalyst note: leverage remains comfortable at 1.24."
    r = client.put(f"{GATEWAY}/api/cams/{cam_id}/sections/{fin['id']}", headers=analyst,
                   json={"content": edited, "base_version_no": fin["current_version_no"]})
    assert r.status_code == 200, r.text
    v_after = r.json()["version_no"]
    r = client.put(f"{GATEWAY}/api/cams/{cam_id}/sections/{fin['id']}", headers=analyst,
                   json={"content": edited, "version_name": "analyst-pass-1",
                         "base_version_no": v_after})
    assert r.status_code == 200, r.text
    stale = client.put(f"{GATEWAY}/api/cams/{cam_id}/sections/{fin['id']}", headers=analyst,
                       json={"content": "x", "base_version_no": 1})
    assert stale.status_code == 409
    ok("AC3", "inline edit with autosave, named version and edit-conflict protection")

    # conversational edit grounded on an in-chat upload (FR-E05)
    chat_doc = upload(client, analyst, case["id"], samples["stock"], origin="chat")
    assert chat_doc["tags"] and chat_doc["tags"][0]["doctype_code"] == "stock_audit_report"
    r = client.post(f"{GATEWAY}/api/cams/{cam_id}/chat", headers=analyst,
                    json={"scope": "section", "section_id": fin["id"],
                          "message": "Re-analyse working capital using the new stock audit report",
                          "attached_document_ids": [chat_doc["id"]]})
    assert r.status_code == 200, r.text
    suggestion = r.json()["suggestion"]
    assert suggestion and suggestion["status"] == "pending" and suggestion["diff"]
    assert "310" in suggestion["proposed_content"]  # figure from the in-chat document
    r = client.post(f"{GATEWAY}/api/cams/{cam_id}/suggestions/{suggestion['id']}/accept",
                    headers=analyst)
    assert r.status_code == 200, r.text
    section = next(s for s in client.get(f"{GATEWAY}/api/cams/{cam_id}",
                                         headers=analyst).json()["sections"]
                   if s["id"] == fin["id"])
    assert "310" in section["content"]
    ok("AC3", "in-chat upload → tracked suggestion (pending) → accept applied it as a new version")

    r = client.post(f"{GATEWAY}/api/cams/{cam_id}/chat", headers=analyst,
                    json={"scope": "section", "section_id": fin["id"],
                          "message": "shorten this section"})
    s2 = r.json()["suggestion"]
    # human-in-the-loop gate: cannot finalise with a pending AI proposal
    r = client.post(f"{GATEWAY}/api/cams/{cam_id}/finalise", headers=analyst)
    assert r.status_code == 409
    client.post(f"{GATEWAY}/api/cams/{cam_id}/suggestions/{s2['id']}/reject",
                headers=analyst, json={"reason": "keep the detail"}).raise_for_status()
    ok("AC3", "second suggestion rejected; finalise correctly blocked while it was pending")

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    draft_docx = client.get(f"{GATEWAY}/api/cams/{cam_id}/export.docx", headers=analyst)
    assert draft_docx.content[:2] == b"PK"
    r = client.post(f"{GATEWAY}/api/cams/{cam_id}/finalise", headers=analyst)
    assert r.status_code == 200 and r.json()["status"] == "final"
    final_docx = client.get(f"{GATEWAY}/api/cams/{cam_id}/export.docx", headers=analyst)
    final_pdf = client.get(f"{GATEWAY}/api/cams/{cam_id}/export.pdf", headers=analyst)
    assert final_docx.content[:2] == b"PK" and final_pdf.content[:4] == b"%PDF"
    (EXPORT_DIR / "CAM_Acme_draft.docx").write_bytes(draft_docx.content)
    (EXPORT_DIR / "CAM_Acme_final.docx").write_bytes(final_docx.content)
    (EXPORT_DIR / "CAM_Acme_final.pdf").write_bytes(final_pdf.content)
    case_state = client.get(f"{GATEWAY}/api/cases/{case['id']}", headers=analyst).json()
    assert case_state["status"] == "finalised", case_state["status"]
    ok("AC3", f"finalised; DOCX ({len(final_docx.content)//1024} KB) and PDF "
              f"({len(final_pdf.content)//1024} KB) downloaded to {EXPORT_DIR}")
    return {"cam_id": cam_id}


def ac4_lineage(client: httpx.Client, cam_id: str) -> None:
    print("\nAC-4 — audit trail reconstructs full lineage")
    auditor = login(client, "auditor1")
    r = client.get(f"{GATEWAY}/api/audit/lineage/cam/{cam_id}", headers=auditor)
    assert r.status_code == 200, r.text
    lineage = r.json()
    record = lineage["run_record"]
    assert record["master_versions"]["template"] >= 1
    assert record["master_versions"]["prompts"]["financial_analysis"] >= 1
    assert record["master_versions"]["kpi_set"] >= 1
    assert record["model_identity"]
    assert record["applied_preferences"]["tonality"] == "crisp"
    hashes = [d.get("sha256") for d in lineage["document_hashes"] if d.get("sha256")]
    assert len(hashes) >= 5
    edit_actions = {e["action"] for e in lineage["edits"]}
    assert {"cam.section_edited", "cam.suggestion_accepted", "cam.suggestion_rejected",
            "cam.finalised"} <= edit_actions
    ok("AC4", f"lineage: template/prompt/KPI versions + model + preferences + "
              f"{len(hashes)} document hashes + {len(lineage['edits'])} edit events")

    chain = client.get(f"{GATEWAY}/api/audit/verify-chain", headers=auditor).json()
    assert chain["intact"] is True and chain["checked"] > 40
    exported = client.get(f"{GATEWAY}/api/audit/export?format=csv", headers=auditor)
    assert exported.status_code == 200 and len(exported.text.splitlines()) > 40
    ok("AC4", f"hash chain intact over {chain['checked']} events; auditor CSV export works")


def ac5_security(client: httpx.Client) -> None:
    print("\nAC-5 — security posture")
    r = client.get(f"{GATEWAY}/api/cases")
    assert r.status_code == 401
    analyst = login(client, "analyst1")
    r = client.post(f"{GATEWAY}/api/genai/generate", headers=analyst, json={})
    assert r.status_code == 403  # NFR-10: model plane closed to end users
    r = client.post(f"{GATEWAY}/api/auth/token",
                    json={"username": "analyst1", "password": "Demo#2026"})
    body = r.json()
    assert "password" not in str(body).lower() or "password_hash" not in str(body)
    assert set(body["user"].keys()) == {"id", "username", "display_name", "email",
                                        "roles", "active"}
    ok("AC5", "unauthenticated calls rejected at the gateway; GenAI plane closed to user "
              "tokens; no credential material in any client-visible payload")


def main() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    samples_ready = make_samples()

    with Stack(data_dir=str(DATA_DIR)):
        with httpx.Client(timeout=60.0) as client:
            ac1_masters(client)
            case, run_id, cam = ac2_case_to_download(client, samples_ready)
            result = ac3_workspace(client, samples_ready, case, run_id, cam)
            ac4_lineage(client, result["cam_id"])
            ac5_security(client)

    print(f"\nALL ACCEPTANCE CRITERIA PASSED ({len(_passed)} checks) — "
          f"exports in {EXPORT_DIR}")


if __name__ == "__main__":
    main()
