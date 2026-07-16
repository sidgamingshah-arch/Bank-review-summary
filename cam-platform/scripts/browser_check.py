"""Browser-based smoke check: drives the real SPA in headless Chromium against
the full running stack — login, masters workbench, case + uploads with
auto-tagging, generation, run progress, CAM workspace with a conversational
suggestion accepted, export, finalise, and the audit screen.

    .venv/bin/pip install playwright          # one-off (browser is pre-installed)
    .venv/bin/python scripts/browser_check.py

Screenshots land in .data-browser/shots/. Exits non-zero on any failed step,
uncaught page error, or console error.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_demo import FIN_TEXT, SANCTION_TEXT, STMT_TEXT  # noqa: E402
from run_stack import ROOT, Stack  # noqa: E402
from seed_demo import GATEWAY, seed  # noqa: E402

DATA_DIR = ROOT / ".data-browser"
SHOTS = DATA_DIR / "shots"
FRONTEND = ROOT / "frontend"
APP = "http://localhost:5173"
PASSWORD = "Demo#2026"
CHROMIUM = "/opt/pw-browsers/chromium"

console_errors: list[str] = []
page_errors: list[str] = []
_step_no = 0


def shot(page, name: str) -> None:
    global _step_no
    _step_no += 1
    page.screenshot(path=str(SHOTS / f"{_step_no:02d}_{name}.png"), full_page=False)
    print(f"  ✔ {name.replace('_', ' ')}  →  shots/{_step_no:02d}_{name}.png")


def make_samples() -> list[Path]:
    from docx import Document as Docx
    from fpdf import FPDF

    samples = DATA_DIR / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(180, 6, FIN_TEXT)
    fin = samples / "Zenith_audited_financials_FY2025.pdf"
    pdf.output(str(fin))
    stmt = samples / "Zenith_bank_statement_Jun2026.txt"
    stmt.write_text(STMT_TEXT)
    docx = Docx()
    docx.add_heading("Sanction Letter", level=1)
    for line in SANCTION_TEXT.split(". "):
        docx.add_paragraph(line)
    sanction = samples / "Zenith_sanction_letter.docx"
    docx.save(str(sanction))
    return [fin, stmt, sanction]


def start_frontend() -> subprocess.Popen:
    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"],
        cwd=str(FRONTEND), stdout=open(DATA_DIR / "vite.log", "w"),
        stderr=subprocess.STDOUT, env={**os.environ})
    deadline = time.monotonic() + 60
    with httpx.Client(timeout=2.0) as client:
        while True:
            try:
                if client.get(APP).status_code == 200:
                    return proc
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                proc.terminate()
                raise RuntimeError("vite dev server did not come up (see .data-browser/vite.log)")
            time.sleep(0.5)


def wire_logging(context) -> None:
    def on_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text[:300])

    context.on("console", on_console)
    context.on("page", lambda p: p.on("pageerror", lambda e: page_errors.append(str(e)[:300])))


def login(browser, username: str):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    wire_logging(context)
    page = context.new_page()
    page.goto(APP)
    page.fill("#login-username", username)
    page.fill("#login-password", PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=15_000)
    return context, page


def check_admin(browser) -> None:
    print("\n[admin1] masters workbench")
    context, page = login(browser, "admin1")
    page.wait_for_selector("text=Prompts", timeout=15_000)
    page.wait_for_selector("text=financial_analysis", timeout=15_000)
    page.click("text=financial_analysis")
    page.wait_for_selector("text=published", timeout=10_000)
    shot(page, "masters_workbench_prompt_versions")
    context.close()


def check_analyst_flow(browser, files: list[Path]) -> None:
    print("\n[analyst1] case → uploads → auto-tags → generation → workspace")
    context, page = login(browser, "analyst1")
    shot(page, "cases_list")

    page.get_by_role("button", name="New case").click()
    page.fill(".modal input", "Zenith Alloys Ltd")
    selects = page.locator(".modal select")
    selects.nth(2).select_option("steel")  # industry (segment/relationship keep defaults)
    shot(page, "new_case_modal")
    page.get_by_role("button", name="Create case").click()
    page.wait_for_selector("h2:has-text('Documents')", timeout=15_000)

    # one multi-select → FE fans out sequential single-file uploads (FR-C02)
    page.set_input_files("input[type=file]", [str(f) for f in files])
    for tag in ("audited_financials", "bank_statements", "sanction_letter"):
        page.wait_for_selector(f"text={tag}", timeout=30_000)
    shot(page, "documents_auto_tagged")

    gen_card = page.locator(".card", has_text="Generate CAM")
    gen_card.locator("select").first.select_option("corp-etb")
    page.wait_for_selector("text=audited_financials", timeout=10_000)  # completeness list
    shot(page, "generation_card_completeness")
    gen_card.get_by_role("button", name="Generate").click()

    page.wait_for_url(lambda url: "/runs/" in url, timeout=15_000)
    page.wait_for_selector("text=Open CAM workspace", timeout=180_000)
    shot(page, "run_complete")

    page.get_by_text("Open CAM workspace").click()
    page.wait_for_selector("text=AI-ASSISTED DRAFT", timeout=15_000)
    page.click(".cam-nav-item:has-text('Financial Analysis')")
    shot(page, "workspace_draft")

    # conversational edit → tracked suggestion → accept (FR-E04/E06)
    page.click("text=This section")
    page.fill("textarea.chat-input", "shorten this section")
    page.press("textarea.chat-input", "Enter")
    page.wait_for_selector("button:has-text('Accept')", timeout=30_000)
    shot(page, "chat_suggestion_pending")
    page.get_by_role("button", name="Accept").first.click()
    page.wait_for_selector("button:has-text('Accept')", state="detached", timeout=15_000)
    shot(page, "chat_suggestion_accepted")

    with page.expect_download(timeout=30_000) as dl:
        page.get_by_role("button", name="Export DOCX").click()
    download = dl.value
    target = SHOTS / download.suggested_filename
    download.save_as(str(target))
    assert target.stat().st_size > 10_000, "empty DOCX download"
    print(f"  ✔ DOCX downloaded through the browser ({target.stat().st_size // 1024} KB)")

    page.get_by_role("button", name="Finalise").first.click()
    page.locator(".modal").get_by_role("button", name="Finalise").click()
    page.wait_for_selector("text=FINAL", timeout=15_000)
    shot(page, "cam_finalised")
    context.close()


def check_auditor(browser) -> None:
    print("\n[auditor1] audit trail")
    context, page = login(browser, "auditor1")
    page.wait_for_selector("h1:has-text('Audit trail')", timeout=15_000)
    page.get_by_role("button", name="Verify chain").click()
    page.wait_for_selector("text=intact", timeout=15_000)
    shot(page, "audit_trail_chain_intact")
    context.close()


def main() -> None:
    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    SHOTS.mkdir(parents=True, exist_ok=True)
    files = make_samples()

    from playwright.sync_api import sync_playwright

    with Stack(data_dir=str(DATA_DIR)):
        with httpx.Client(timeout=30.0) as client:
            seed(client)
        print("masters seeded through the APIs")
        vite = start_frontend()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=CHROMIUM, headless=True,
                                            args=["--no-sandbox"])
                check_admin(browser)
                check_analyst_flow(browser, files)
                check_auditor(browser)
                browser.close()
        finally:
            vite.terminate()

    print(f"\nconsole errors: {len(console_errors)}  page errors: {len(page_errors)}")
    for err in console_errors + page_errors:
        print(f"  ✘ {err}")
    if console_errors or page_errors:
        sys.exit(1)
    print(f"BROWSER CHECK PASSED — screenshots in {SHOTS}")


if __name__ == "__main__":
    main()
