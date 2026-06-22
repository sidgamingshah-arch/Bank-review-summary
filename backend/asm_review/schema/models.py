"""Section models that mirror the ASM review-note template.

Each top-level section is its own model so it can be the ``output_format`` of one
focused structured-output call. Monetary/quantitative cells are typed as
``Field[str]`` (not float) so the exact source representation -- "NIL", "-",
ranges, thousands separators -- survives verbatim; numeric work (e.g. deviation
%) is done defensively in ``reason.py``.
"""

from __future__ import annotations

from pydantic import BaseModel

from asm_review.schema.fields import Field


# --- Header & identifiers -------------------------------------------------
class HeaderBlock(BaseModel):
    date_of_review: Field[str]
    br_code: Field[str]
    br_name: Field[str]
    ro: Field[str]
    cif_id: Field[str]
    ac_name: Field[str]


# --- Audit details --------------------------------------------------------
class AuditDetails(BaseModel):
    name_of_asm_auditor: Field[str]
    appointed_by: Field[str]
    audit_as_on_quarter: Field[str]
    report_date: Field[str]


# --- Account details ------------------------------------------------------
class AccountDetails(BaseModel):
    constitution: Field[str]
    nature_of_activity: Field[str]
    banking_arrangement: Field[str]
    bank_sanction_ref: Field[str]
    business_vertical: Field[str]
    bank_rating: Field[str]
    external_rating: Field[str]


# --- I. Banking exposure of the customer ----------------------------------
class BankingExposureRow(BaseModel):
    lender: Field[str]
    fbwc_os_balance: Field[str]
    nfbwc_os_balance: Field[str]
    term_loan: Field[str]


class BankingExposure(BaseModel):
    rows: list[BankingExposureRow]
    total_fbwc: Field[str]
    total_nfbwc: Field[str]
    total_term_loan: Field[str]


# --- II. Exposure with the bank -------------------------------------------
class BankExposureRow(BaseModel):
    facility: Field[str]
    limit_crs: Field[str]
    dp_crs: Field[str]
    balance_crs: Field[str]


class BankExposure(BaseModel):
    rows: list[BankExposureRow]
    footnote: Field[str]


# --- III. Primary security ------------------------------------------------
class PrimarySecurity(BaseModel):
    primary_security: Field[str]
    margin: Field[str]


# --- IV. Main observations in ASM report ----------------------------------
class MainObservations(BaseModel):
    business_positions: Field[str]
    networth_level: Field[str]
    financial_ratios: Field[str]
    sales_purchase_profit_levels: Field[str]
    asset_and_liabilities: Field[str]
    contingent_statutory_liabilities: Field[str]
    sundry_creditors: Field[str]
    sundry_debtors_book_debts: Field[str]
    stock_raw_materials_wip: Field[str]
    fixed_assets: Field[str]
    bank_borrowings_limit_utilisations_overdues: Field[str]
    bank_account_operations: Field[str]
    cash_flow_and_alm: Field[str]
    high_value_related_party_transactions: Field[str]
    any_other_observations: Field[str]


# --- V. Insurance ---------------------------------------------------------
class Insurance(BaseModel):
    whether_adequate_insurance_present: Field[str]
    validity_of_insurance: Field[str]
    whether_banks_lien_noted: Field[str]
    whether_all_locations_covered: Field[str]
    whether_all_risks_covered: Field[str]
    other_remarks: Field[str]


# --- VI. Observations on project under implementation ---------------------
class ProjectObservations(BaseModel):
    deviations_in_project_progress: Field[str]
    scheduled_dcco_and_months_remaining: Field[str]
    any_other_observations: Field[str]


# --- VII. Drawing power computation ---------------------------------------
class DrawingPowerRow(BaseModel):
    particulars: Field[str]
    value: Field[str]


class DrawingPower(BaseModel):
    rows: list[DrawingPowerRow]


# --- VIII. Position of account as on date of audit ------------------------
class AccountPositionRow(BaseModel):
    nature: Field[str]
    total_limit: Field[str]
    total_dp: Field[str]
    total_balance_os: Field[str]
    bank_limit: Field[str]
    bank_dp: Field[str]
    bank_balance_os: Field[str]
    remarks: Field[str]
    deviation_pct: Field[str]


class AccountPosition(BaseModel):
    rows: list[AccountPositionRow]


# --- IX. CRILC & MCA verification -----------------------------------------
class CrilcMcaVerification(BaseModel):
    maintaining_current_accounts_other_banks: Field[str]
    names_of_banks: Field[str]
    whether_permission_obtained: Field[str]
    whether_roc_charges_filed_properly: Field[str]


# --- X. Remarks on ASM report ---------------------------------------------
class PrevQuarterComparisonRow(BaseModel):
    previous_quarter_comment: Field[str]
    current_quarter_status: Field[str]


class Remarks(BaseModel):
    prev_quarter_comparison: list[PrevQuarterComparisonRow]
    critical_observations: Field[str]
    other_observations: Field[str]
    for_sanctioning_authority: Field[str]
    for_reviewing_authority: Field[str]


# --- Whole document -------------------------------------------------------
class ReviewNote(BaseModel):
    header: HeaderBlock
    audit_details: AuditDetails
    account_details: AccountDetails
    banking_exposure: BankingExposure
    bank_exposure: BankExposure
    primary_security: PrimarySecurity
    main_observations: MainObservations
    insurance: Insurance
    project_observations: ProjectObservations
    drawing_power: DrawingPower
    account_position: AccountPosition
    crilc_mca: CrilcMcaVerification
    remarks: Remarks


# Ordered (ReviewNote attribute, section model) pairs. Drives both the
# extraction loop and the render order; instructions are joined by attr name.
SECTION_ORDER: list[tuple[str, type[BaseModel]]] = [
    ("header", HeaderBlock),
    ("audit_details", AuditDetails),
    ("account_details", AccountDetails),
    ("banking_exposure", BankingExposure),
    ("bank_exposure", BankExposure),
    ("primary_security", PrimarySecurity),
    ("main_observations", MainObservations),
    ("insurance", Insurance),
    ("project_observations", ProjectObservations),
    ("drawing_power", DrawingPower),
    ("account_position", AccountPosition),
    ("crilc_mca", CrilcMcaVerification),
    ("remarks", Remarks),
]
