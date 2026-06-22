"""Provenance verification: matching, downgrading, missing flags, skip behaviour."""

from __future__ import annotations

from asm_review.verify import verify_note


def _corpus_with_quotes() -> str:
    # Must exceed the min-corpus threshold and contain a few evidence quotes verbatim.
    filler = (
        "ASM stock audit report for the borrower. The auditor reviewed stock, "
        "book debts and bank operations during the quarter. " * 4
    )
    return (
        filler
        + " Branch Code: 001. CIF ID: CIF123456. Borrower: Acme Industries Pvt Ltd. "
        + filler
    )


def test_verifies_quotes_and_downgrades_unverified(make_note) -> None:
    note = make_note()
    report = verify_note(note, _corpus_with_quotes())

    assert report.verification_performed is True
    # br_code / cif_id / ac_name quotes are present in the corpus -> verified.
    assert report.verified_fields >= 3
    # A present field whose quote is absent from the corpus is downgraded.
    assert note.audit_details.name_of_asm_auditor.confidence == "low"
    # Missing fields are reported (e.g. external_rating).
    assert report.missing
    assert any("external_rating" in flag.path for flag in report.missing)


def test_skips_verification_for_tiny_corpus(make_note) -> None:
    note = make_note()
    report = verify_note(note, "scanned, no text")
    assert report.verification_performed is False
    assert report.unverified == []  # nothing downgraded when we can't verify
    # A field that started high stays high (no downgrade happened).
    assert note.header.br_code.confidence == "high"
    assert report.present_fields > 0
