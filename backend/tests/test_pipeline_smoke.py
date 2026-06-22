"""End-to-end pipeline with an injected fake client (no network / no API key)."""

from __future__ import annotations

from asm_review.config import Settings
from asm_review.pipeline import run_pipeline


def test_pipeline_end_to_end_with_fake_client(fake_client, tmp_path) -> None:
    src = tmp_path / "asm.txt"
    src.write_text(
        "ASM stock audit report. Branch Code: 001. CIF ID: CIF123456. "
        "Borrower: Acme Industries Pvt Ltd. "
        + ("Filler content for verification corpus. " * 10),
        encoding="utf-8",
    )
    out = tmp_path / "out" / "note.docx"

    result = run_pipeline([src], out, settings=Settings(), client=fake_client, model="fake")

    assert result.docx_path.exists()
    assert result.note.header.ac_name.value == "Acme Industries Pvt Ltd"
    # All 13 sections were requested from the (fake) model.
    assert result.usage["calls"] == 13
    # Reasoning normalised the bare deviation number to a percentage.
    assert result.note.account_position.rows[0].deviation_pct.value == "0%"
    # QC ran over the assembled note.
    assert result.qc.total_fields > 0
    assert result.qc.present_fields > 0
    assert result.doc_names == ["asm.txt"]
