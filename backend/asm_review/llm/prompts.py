"""System prompt + per-section extraction instructions.

The system prompt is byte-stable across all section calls so it sits inside the
cached prefix. Each section instruction is appended *after* the cached source
documents and varies per call.
"""

from __future__ import annotations

GLOSSARY = """\
Domain glossary (Indian banking / stock-audit context):
- ASM: Agency for Specialised Monitoring (external auditor appointed by the bank).
- FBWC: Fund-Based Working Capital. NFBWC: Non-Fund-Based Working Capital. TL: Term Loan.
- DP: Drawing Power. o/s: outstanding. b/s: balance sheet.
- CIF: Customer Information File. RO: Regional Office. RM/BR: Relationship Manager / Branch.
- CRILC: Central Repository of Information on Large Credits. MCA: Ministry of Corporate Affairs.
- ROC: Registrar of Companies. MBA: Multiple Banking Arrangement.
- DCCO: Date of Commencement of Commercial Operations. ALM: Asset-Liability Management.
- Amounts are usually in Rupees crore (crs)."""

SYSTEM_PROMPT = f"""\
You are a meticulous credit-risk data-extraction assistant for a bank's risk team.
You read the attached source documents (an ASM / stock-audit report, and possibly a
tracker / base-data sheet, sanction letter, insurance documents, or a previous-quarter
review note) and extract values for a structured ASM Review Note.

{GLOSSARY}

Rules — follow exactly:
1. Extract ONLY from the attached documents. Never invent, infer beyond the text, or
   use outside knowledge to fill a value.
2. For every field, return the wrapper object with:
   - value: the extracted value, copied faithfully (keep units, "NIL", "-", numbers and
     separators as written). Use null when the document does not contain it.
   - found: true only if you located it in the documents; false otherwise.
   - confidence: "high" if stated explicitly and unambiguously; "medium" if it required
     light interpretation; "low" if uncertain.
   - source_document: the file name (or best identifier) the value came from, else null.
   - page: the 1-based page number where you found it, else null.
   - evidence_quote: a SHORT verbatim quote (copied character-for-character from the
     document) that contains the value, else null. Do not paraphrase the quote.
3. Do not guess identifiers, codes, dates, or amounts. A wrong value is worse than a
   blank — when in doubt set found=false and value=null.
4. Treat fields the template marks "to be fed from tracker/base data" or "manually
   entered" exactly like any other field: extract them if present in the documents,
   otherwise leave them not-found.
5. Be exhaustive for table sections: return one row object per row present in the source
   table. If a table is absent, return an empty rows list.
"""


# Keyed by ReviewNote attribute name (see schema.models.SECTION_ORDER).
SECTION_INSTRUCTIONS: dict[str, str] = {
    "header": (
        "Extract the review-note header / identifiers: date the report review is done "
        "(date_of_review), branch code (br_code), branch name (br_name), Regional Office "
        "(ro), CIF ID (cif_id), and the account/borrower name (ac_name). These often come "
        "from a tracker/base-data sheet or the cover of the report."
    ),
    "audit_details": (
        "Extract AUDIT DETAILS: name of the ASM auditor (name_of_asm_auditor), who "
        "appointed the auditor (appointed_by), the quarter the audit is 'as on' "
        "(audit_as_on_quarter), and the report date (report_date)."
    ),
    "account_details": (
        "Extract ACCOUNT DETAILS: constitution (e.g. Pvt Ltd / Partnership), nature of "
        "activity / business, banking arrangement (e.g. Sole / Consortium / MBA), the bank "
        "sanction reference (bank_sanction_ref), business vertical, the bank's internal "
        "rating (bank_rating), and the external rating (external_rating)."
    ),
    "banking_exposure": (
        "Extract Section I — DETAILS OF BANKING EXPOSURE OF THE CUSTOMER. For each lender, "
        "return a row with lender name, FBWC outstanding balance (fbwc_os_balance), NFBWC "
        "outstanding balance (nfbwc_os_balance), and term loan (term_loan). Also return the "
        "TOTAL row values (total_fbwc, total_nfbwc, total_term_loan)."
    ),
    "bank_exposure": (
        "Extract Section II — DETAILS OF EXPOSURE WITH BANK. One row per facility with: "
        "facility name, sanctioned limit in crore (limit_crs), drawing power as on date "
        "(dp_crs), and balance as on date (balance_crs). Capture any footnote marked '*'."
    ),
    "primary_security": (
        "Extract Section III — DETAILS OF PRIMARY SECURITY WITH BANK AS PER SANCTION TERMS: "
        "the primary security description (primary_security) and the margin (margin)."
    ),
    "main_observations": (
        "Extract Section IV — MAIN OBSERVATIONS IN ASM REPORT. Summarise the auditor's "
        "observations into each field, staying faithful to the report's wording: business "
        "position, net-worth level, financial ratios, sales/purchase/profit levels, assets "
        "and liabilities, contingent & statutory liabilities, sundry creditors, sundry "
        "debtors / book debts, stock / raw materials / WIP, fixed assets, bank borrowings / "
        "limit utilisation / overdues, bank account operations, cash flow & ALM, high-value "
        "transactions with sister/associate concerns & related parties, and any other "
        "observations by the auditor."
    ),
    "insurance": (
        "Extract Section V — Insurance: whether adequate insurance is present, validity of "
        "insurance, whether the bank's lien is noted, whether all locations are covered, "
        "whether all risks are covered, and any other remarks."
    ),
    "project_observations": (
        "Extract Section VI — OBSERVATIONS ON PROJECT UNDER IMPLEMENTATION (only if the "
        "account has a project under implementation): deviations in project progress vs "
        "timelines and amount disbursed; scheduled DCCO and number of months remaining to "
        "DCCO; and any other observations by the auditor. Leave fields not-found if there is "
        "no project under implementation."
    ),
    "drawing_power": (
        "Extract Section VII — DRAWING POWER COMPUTATION. Return one row per line item of "
        "the DP computation table, each with its particulars and value."
    ),
    "account_position": (
        "Extract Section VIII — POSITION OF ACCOUNT AS ON DATE OF AUDIT. One row per facility "
        "nature with: nature, total limit (Mul/Con), total DP (Mul/Con), total balance "
        "outstanding as on audit date, the BANK's limit, BANK's DP, BANK's balance "
        "outstanding, remarks, and deviation % (deviation_pct) if stated in the report. Only "
        "report deviation_pct if it is explicitly given or directly computable from figures "
        "in the document; otherwise leave it not-found."
    ),
    "crilc_mca": (
        "Extract Section IX — VERIFICATION FROM CRILC AND MCA DATA: whether the party "
        "maintains current accounts with other banks (maintaining_current_accounts_other_"
        "banks), if yes the names of those banks (names_of_banks), whether permission was "
        "obtained (whether_permission_obtained), and whether ROC charges are filed properly "
        "(whether_roc_charges_filed_properly)."
    ),
    "remarks": (
        "Extract Section X — REMARKS ON ASM REPORT, using reasoning where needed:\n"
        "- prev_quarter_comparison: ONLY if a previous-quarter ASM report/note is among the "
        "documents, list each prior-quarter critical comment with its status in the current "
        "quarter. If there is no previous-quarter document, return an empty list.\n"
        "- critical_observations: the auditor's critical observations that must be carried to "
        "the closure letter / review note.\n"
        "- other_observations: other (non-critical) observations.\n"
        "- for_sanctioning_authority: points for the sanctioning authority / business vertical.\n"
        "- for_reviewing_authority: any internal information for the reviewing authority.\n"
        "Base every point strictly on the documents."
    ),
}
