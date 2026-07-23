"""Live-path smoke test: drive ONE real generation through the whole stack
against an OpenAI-compatible endpoint (the bundled fake server), proving that
CAM_LLM_PROVIDER=openai works end-to-end (orchestration → gateway → genai →
HTTP). Also exercises the Excel bulk-upload endpoints live.

    python scripts/live_check.py

Uses a fresh data dir and its own fake endpoint; not part of the unit suite.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
PYTHON = str(ROOT / ".venv" / "bin" / "python")
FAKE_PORT = 8909
DATA_DIR = ROOT / ".data-livecheck"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

FIN = ("Acme Steel Ltd Audited Financial Statements FY2025. Auditor's report unqualified. "
       "Balance sheet and profit and loss. Revenue Rs. 4210 crore, up 12.5%. EBITDA margin "
       "18.2%. Net debt Rs. 950 crore. Cash flow from operations Rs. 610 crore.")
STMT = ("Statement of account - Acme Steel Ltd. Closing balance Rs. 12.4 crore. Average "
        "utilisation 74%. No overdrawals.")
SANCTION = ("Sanction letter facility terms and conditions for Acme Steel Ltd. Working capital "
            "limit Rs. 400 crore. Covenant: net debt to EBITDA not to exceed 3.5.")


def _wait(url: str, tries: int = 60) -> bool:
    with httpx.Client(timeout=2.0) as c:
        for _ in range(tries):
            try:
                if c.get(url).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    return False


def _bulk_workbook() -> bytes:
    from cam.services.master_config import xlsx_io
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_io.build_template_workbook()))
    wb["industries"].append(["svc", "Services", "live_it", "IT Services Live"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run() -> int:
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    fake = subprocess.Popen([PYTHON, str(ROOT / "scripts" / "fake_openai_server.py"),
                             "--port", str(FAKE_PORT)], cwd=str(ROOT))
    stack = None
    try:
        if not _wait(f"http://127.0.0.1:{FAKE_PORT}/healthz"):
            print("FAIL: fake endpoint did not start")
            return 1
        print(f"fake OpenAI-compatible endpoint up on :{FAKE_PORT}")

        os.environ.update({
            "CAM_LLM_PROVIDER": "openai",
            "CAM_GENAI_BASE_URL": f"http://127.0.0.1:{FAKE_PORT}/v1",
            "CAM_GENAI_MODEL": "fake-live-1",
            "CAM_JWT_SECRET": "live-check-secret-abcdefghijklmnopqrstuvwx",
        })
        from run_stack import Stack
        from seed_demo import GATEWAY, login, seed

        stack = Stack(data_dir=str(DATA_DIR))
        stack.start()

        with httpx.Client(timeout=120.0) as client:
            seed(client)
            print("seeded masters (maker-checker)")
            admin1 = login(client, "admin1")
            analyst = login(client, "analyst1")

            # --- bulk-upload endpoints, live ---
            r = client.get(f"{GATEWAY}/api/masters/bulk-template", headers=admin1)
            assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"], r.text
            r = client.post(f"{GATEWAY}/api/masters/bulk-upload", headers=admin1,
                            files={"file": ("m.xlsx", _bulk_workbook(), XLSX_MEDIA)})
            assert r.status_code == 200, r.text
            assert any("industry:live_it" in c["entry"] for c in r.json()["created"]), r.json()
            print("bulk-upload created a draft industry:live_it")

            # --- enable the negative-news connector (mock feed, no URL) ---
            client.put(f"{GATEWAY}/api/masters/settings", headers=admin1,
                       json={"connectors_news_enabled": True}).raise_for_status()

            # --- a real case → docs → run against the live endpoint ---
            case = client.post(f"{GATEWAY}/api/cases", headers=analyst, json={
                "borrower_name": "Acme Steel Ltd", "segment": "corporate",
                "relationship": "etb", "industry_code": "steel"}).json()
            cid = case["id"]
            for text, name, doctype in [(FIN, "fin.txt", "audited_financials"),
                                        (STMT, "stmt.txt", "bank_statements"),
                                        (SANCTION, "sanction.txt", "sanction_letter")]:
                doc = client.post(f"{GATEWAY}/api/cases/{cid}/documents", headers=analyst,
                                  files={"file": (name, text.encode())}).json()
                # explicit user tag -> deterministic completeness regardless of tagging mode
                client.post(f"{GATEWAY}/api/documents/{doc['id']}/tags", headers=analyst,
                            json={"doctype_code": doctype}).raise_for_status()

            r = client.post(f"{GATEWAY}/api/runs", headers=analyst,
                            json={"case_id": cid, "template_key": "corp-etb"})
            assert r.status_code == 202, r.text
            run_id = r.json()["id"]

            deadline = time.monotonic() + 60
            run_rec = client.get(f"{GATEWAY}/api/runs/{run_id}", headers=analyst).json()
            while run_rec["status"] in ("queued", "running"):
                assert time.monotonic() < deadline, f"run stuck at {run_rec['status']}"
                time.sleep(0.5)
                run_rec = client.get(f"{GATEWAY}/api/runs/{run_id}", headers=analyst).json()

            # --- assertions: the LIVE endpoint actually produced the CAM ---
            assert run_rec["status"] in ("complete", "partial"), run_rec["status"]
            assert run_rec["model_identity"] == "fake-live-1", run_rec["model_identity"]
            tokens_out = sum(s["tokens_out"] for s in run_rec["sections"])
            assert tokens_out > 0, "no output tokens recorded — endpoint not exercised"
            assert run_rec["cam_id"], "no CAM handed off"

            cam = client.get(f"{GATEWAY}/api/cams/{run_rec['cam_id']}", headers=analyst).json()
            drafted = [s for s in cam["sections"]
                       if s["section_code"] != "_gaps" and "Fake-endpoint" in s["content"]]
            assert drafted, "no section carries live-endpoint output"

            print(f"live run complete: model={run_rec['model_identity']} "
                  f"sections={len(cam['sections'])} tokens_out={tokens_out} "
                  f"live-drafted={len(drafted)}")
            print("PASS: CAM_LLM_PROVIDER=openai works end-to-end")
            return 0
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        if stack:
            stack.stop()
        fake.terminate()
        try:
            fake.wait(timeout=5)
        except subprocess.TimeoutExpired:
            fake.kill()


if __name__ == "__main__":
    sys.exit(run())
