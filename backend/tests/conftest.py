"""Shared test fixtures: a realistic sample ReviewNote and a fake Claude client."""

from __future__ import annotations

import types

import pytest

from asm_review.schema.fields import Field
from asm_review.schema.models import (
    AccountDetails,
    AccountPosition,
    AccountPositionRow,
    AuditDetails,
    BankExposure,
    BankExposureRow,
    BankingExposure,
    BankingExposureRow,
    CrilcMcaVerification,
    DrawingPower,
    DrawingPowerRow,
    HeaderBlock,
    Insurance,
    MainObservations,
    PrimarySecurity,
    ProjectObservations,
    Remarks,
    ReviewNote,
    SECTION_ORDER,
)


def f(value: str, quote: str | None = None, conf: str = "high") -> Field[str]:
    return Field[str].of(
        value, confidence=conf, source_document="asm.pdf", page=1, evidence_quote=quote
    )


def fm() -> Field[str]:
    return Field[str].missing()


def make_sample_note() -> ReviewNote:
    """A fresh, mostly-populated note (a few fields intentionally missing)."""
    return ReviewNote(
        header=HeaderBlock(
            date_of_review=f("22-06-2026"),
            br_code=f("001", "Branch Code: 001"),
            br_name=f("Main Branch", "Branch: Main Branch"),
            ro=f("West RO"),
            cif_id=f("CIF123456", "CIF ID: CIF123456"),
            ac_name=f("Acme Industries Pvt Ltd", "Acme Industries Pvt Ltd"),
        ),
        audit_details=AuditDetails(
            name_of_asm_auditor=f("XYZ & Associates"),
            appointed_by=f("Bank"),
            audit_as_on_quarter=f("Q4 FY2025-26"),
            report_date=f("15-04-2026"),
        ),
        account_details=AccountDetails(
            constitution=f("Private Limited"),
            nature_of_activity=f("Manufacturing of auto components"),
            banking_arrangement=f("Consortium"),
            bank_sanction_ref=f("SANC/2025/1234"),
            business_vertical=f("Mid Corporate"),
            bank_rating=f("BB+"),
            external_rating=fm(),
        ),
        banking_exposure=BankingExposure(
            rows=[
                BankingExposureRow(
                    lender=f("Our Bank"),
                    fbwc_os_balance=f("10.00"),
                    nfbwc_os_balance=f("2.00"),
                    term_loan=f("5.00"),
                )
            ],
            total_fbwc=f("10.00"),
            total_nfbwc=f("2.00"),
            total_term_loan=f("5.00"),
        ),
        bank_exposure=BankExposure(
            rows=[
                BankExposureRow(
                    facility=f("Cash Credit"),
                    limit_crs=f("10.00"),
                    dp_crs=f("9.50"),
                    balance_crs=f("8.75"),
                )
            ],
            footnote=fm(),
        ),
        primary_security=PrimarySecurity(
            primary_security=f("Stock and book debts"), margin=f("25%")
        ),
        main_observations=MainObservations(
            business_positions=f("Stable operations"),
            networth_level=f("Positive and growing"),
            financial_ratios=f("Current ratio 1.3"),
            sales_purchase_profit_levels=f("Sales up 12% YoY"),
            asset_and_liabilities=f("No major change"),
            contingent_statutory_liabilities=f("None reported"),
            sundry_creditors=f("45 days"),
            sundry_debtors_book_debts=f("60 days"),
            stock_raw_materials_wip=f("Adequate stock levels"),
            fixed_assets=f("No disposals"),
            bank_borrowings_limit_utilisations_overdues=f("Within limits"),
            bank_account_operations=f("Satisfactory"),
            cash_flow_and_alm=f("Positive cash flow"),
            high_value_related_party_transactions=fm(),
            any_other_observations=fm(),
        ),
        insurance=Insurance(
            whether_adequate_insurance_present=f("Yes"),
            validity_of_insurance=f("Valid till 31-03-2027"),
            whether_banks_lien_noted=f("Yes"),
            whether_all_locations_covered=f("Yes"),
            whether_all_risks_covered=f("Yes"),
            other_remarks=fm(),
        ),
        project_observations=ProjectObservations(
            deviations_in_project_progress=fm(),
            scheduled_dcco_and_months_remaining=fm(),
            any_other_observations=fm(),
        ),
        drawing_power=DrawingPower(
            rows=[
                DrawingPowerRow(particulars=f("Stock"), value=f("12.00")),
                DrawingPowerRow(particulars=f("Less: Margin 25%"), value=f("3.00")),
            ]
        ),
        account_position=AccountPosition(
            rows=[
                AccountPositionRow(
                    nature=f("FBWC"),
                    total_limit=f("10.00"),
                    total_dp=f("9.50"),
                    total_balance_os=f("8.75"),
                    bank_limit=f("10.00"),
                    bank_dp=f("9.50"),
                    bank_balance_os=f("8.75"),
                    remarks=f("Within DP"),
                    deviation_pct=f("0"),  # bare number -> reasoning tidies to "0%"
                )
            ]
        ),
        crilc_mca=CrilcMcaVerification(
            maintaining_current_accounts_other_banks=f("Yes within the MBA"),
            names_of_banks=f("Bank A, Bank B"),
            whether_permission_obtained=f("MBA"),
            whether_roc_charges_filed_properly=f("Yes"),
        ),
        remarks=Remarks(
            prev_quarter_comparison=[],
            critical_observations=f("Stock statement delayed by 20 days"),
            other_observations=f("Minor discrepancy in debtor ageing"),
            for_sanctioning_authority=fm(),
            for_reviewing_authority=fm(),
        ),
    )


class _FakeMessages:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping

    def parse(self, *, output_format, **_kwargs):
        instance = self._mapping[output_format]
        usage = types.SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=80,
        )
        return types.SimpleNamespace(
            parsed_output=instance, usage=usage, stop_reason="end_turn"
        )


class FakeClient:
    """Returns the matching section of a prebuilt note for each output_format."""

    def __init__(self, note: ReviewNote) -> None:
        mapping = {cls: getattr(note, attr) for attr, cls in SECTION_ORDER}
        self.messages = _FakeMessages(mapping)


@pytest.fixture
def make_note():
    return make_sample_note


@pytest.fixture
def sample_note() -> ReviewNote:
    return make_sample_note()


@pytest.fixture
def fake_client(sample_note: ReviewNote) -> FakeClient:
    return FakeClient(sample_note)
