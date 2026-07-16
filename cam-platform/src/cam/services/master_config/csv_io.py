"""FR-A12: bulk KPI maintenance via CSV upload/download with a validation report.

CSV columns: industry_code, kpi_code, kpi_name, definition, unit, polarity,
benchmark, sections (pipe-separated section codes). Rows group into one
kpi-set payload per industry; each affected industry gets a NEW DRAFT version
(bulk upload never bypasses maker-checker).
"""
from __future__ import annotations

import csv
import io

CSV_HEADERS = ["industry_code", "kpi_code", "kpi_name", "definition", "unit",
               "polarity", "benchmark", "sections"]

POLARITIES = {"higher_better", "lower_better"}


def parse_kpi_csv(raw: bytes) -> tuple[dict[str, list[dict]], list[dict]]:
    """Return ({industry_code: [kpi, ...]}, [{row, message}, ...])."""
    errors: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {}, [{"row": 0, "message": "file is not valid UTF-8"}]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or [h.strip() for h in reader.fieldnames] != CSV_HEADERS:
        return {}, [{"row": 0, "message": f"header must be exactly: {','.join(CSV_HEADERS)}"}]

    for line_no, row in enumerate(reader, start=2):
        industry = (row.get("industry_code") or "").strip()
        code = (row.get("kpi_code") or "").strip()
        name = (row.get("kpi_name") or "").strip()
        polarity = (row.get("polarity") or "").strip()
        problems = []
        if not industry:
            problems.append("industry_code is required")
        if not code:
            problems.append("kpi_code is required")
        if not name:
            problems.append("kpi_name is required")
        if polarity not in POLARITIES:
            problems.append(f"polarity must be one of {sorted(POLARITIES)}")
        if problems:
            errors.append({"row": line_no, "message": "; ".join(problems)})
            continue
        grouped.setdefault(industry, []).append({
            "code": code, "name": name,
            "definition": (row.get("definition") or "").strip(),
            "unit": (row.get("unit") or "").strip(),
            "polarity": polarity,
            "benchmark": (row.get("benchmark") or "").strip() or None,
            "sections": [s.strip() for s in (row.get("sections") or "").split("|") if s.strip()],
        })
    return grouped, errors


def render_kpi_csv(kpi_sets: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)
    for payload in kpi_sets:
        for kpi in payload.get("kpis", []):
            writer.writerow([
                payload.get("industry_code", ""), kpi.get("code", ""), kpi.get("name", ""),
                kpi.get("definition", ""), kpi.get("unit", ""), kpi.get("polarity", ""),
                kpi.get("benchmark") or "", "|".join(kpi.get("sections", [])),
            ])
    return buf.getvalue()
