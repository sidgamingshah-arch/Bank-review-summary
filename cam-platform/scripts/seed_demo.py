"""Seed the platform with a realistic master configuration — entirely through
the public APIs under maker-checker control (admin1 drafts + submits, admin2
approves), exactly as a business admin would (BRD AC-1).

    python scripts/seed_demo.py   # against a running stack (scripts/run_stack.py)
"""
from __future__ import annotations

import sys

import httpx

GATEWAY = "http://localhost:8080"
PASSWORD = "Demo#2026"


def login(client: httpx.Client, username: str) -> dict:
    r = client.post(f"{GATEWAY}/api/auth/token",
                    json={"username": username, "password": PASSWORD})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def publish(client: httpx.Client, maker: dict, checker: dict, mtype: str, key: str,
            payload: dict, note: str = "seed") -> int:
    """Create (or add a version) → submit → approve. Returns the published version."""
    r = client.post(f"{GATEWAY}/api/masters/{mtype}", headers=maker,
                    json={"key": key, "payload": payload, "change_note": note})
    if r.status_code == 409:  # item exists — add a new version instead
        r = client.post(f"{GATEWAY}/api/masters/{mtype}/{key}/versions", headers=maker,
                        json={"payload": payload, "change_note": note})
        r.raise_for_status()
        version = r.json()["version_no"]
    else:
        r.raise_for_status()
        version = r.json()["versions"][-1]["version_no"]
    client.post(f"{GATEWAY}/api/masters/{mtype}/{key}/versions/{version}/submit",
                headers=maker).raise_for_status()
    client.post(f"{GATEWAY}/api/masters/{mtype}/{key}/versions/{version}/approve",
                headers=checker).raise_for_status()
    return version


DOCTYPES = {
    "audited_financials": ("Audited financials", ["annual report", "audited accounts"],
                           ["balance sheet", "profit and loss", "auditor's report", "cash flow"]),
    "provisional_financials": ("Provisional financials", ["provisionals"],
                               ["provisional", "unaudited"]),
    "bank_statements": ("Bank statements", ["account statement"],
                        ["statement of account", "closing balance", "utilisation"]),
    "sanction_letter": ("Sanction letter", ["facility letter"],
                        ["sanction", "facility", "terms and conditions", "covenant"]),
    "credit_bureau_report": ("Credit bureau report", ["cibil", "bureau"],
                             ["bureau", "credit score", "dpd"]),
    "project_report": ("Project report", ["dpr"], ["project cost", "dscr", "implementation"]),
    "stock_audit_report": ("Stock audit report", ["asm report"],
                           ["stock audit", "drawing power", "inventory"]),
    "previous_cam": ("Previous CAM", ["last cam"], ["credit assessment memo", "previous review"]),
}

INDUSTRIES = [
    ("mfg", "Manufacturing", "steel", "Steel"),
    ("mfg", "Manufacturing", "auto_components", "Auto Components"),
    ("svc", "Services", "it_services", "IT Services"),
    ("infra", "Infrastructure", "roads", "Roads & Highways"),
]

KPI_SETS = {
    "steel": [
        {"code": "ebitda_per_tonne", "name": "EBITDA per tonne", "unit": "INR/t",
         "definition": "Operating profit divided by tonnes sold", "polarity": "higher_better",
         "benchmark": "4500", "sections": ["industry_analysis", "financial_analysis"]},
        {"code": "capacity_utilisation", "name": "Capacity utilisation", "unit": "%",
         "definition": "Production as a share of installed capacity",
         "polarity": "higher_better", "benchmark": "80", "sections": ["industry_analysis"]},
        {"code": "net_debt_ebitda", "name": "Net debt / EBITDA", "unit": "x",
         "definition": "Leverage on operating cashflow", "polarity": "lower_better",
         "benchmark": "3.5", "sections": ["financial_analysis", "risk_mitigants"]},
    ],
    "it_services": [
        {"code": "attrition", "name": "Attrition rate", "unit": "%",
         "definition": "Trailing 12m voluntary attrition", "polarity": "lower_better",
         "benchmark": "18", "sections": ["industry_analysis"]},
        {"code": "utilisation", "name": "Billable utilisation", "unit": "%",
         "definition": "Billable hours over available hours", "polarity": "higher_better",
         "benchmark": "78", "sections": ["financial_analysis"]},
    ],
}

GLOBAL_RULES = {
    "section_code": "global_standing_rules", "section_name": "Global standing rules",
    "scope": "global", "source_doc_types": [], "uses_industry_kpis": False,
    "prompt_text": ("House-wide rules for every CAM section: use formal UK-English credit "
                    "language; never speculate on external ratings; state amounts in INR "
                    "crore as reported in sources; disclose every assumption explicitly."),
}

# Governed standing rules for the pipeline agents (tunable like any prompt,
# maker-checker controlled, carried by the export bundle).
AGENT_RULES = {
    "agent_extraction_rules":
        "Prefer audited figures over provisional ones when both exist; capture the "
        "financial year or period with every figure; never compute derived ratios.",
    "agent_summarisation_rules":
        "Every paragraph must be attributable to the extracted facts; keep covenant "
        "language verbatim from the sanction documents.",
    "agent_materiality_rules":
        "Treat as material: any exposure above INR 100 crore, any covenant with "
        "headroom under 10%, every KPI in the industry framework, and any "
        "year-on-year deterioration above 20%.",
    "agent_consistency_rules":
        "Figures must match the extracted facts exactly (no rounding drift); flag any "
        "statement that contradicts another section's figures for the same item.",
}

SECTION_PROMPTS = [
    ("exec_summary", "Executive Summary & Recommendation",
     "Draft the executive summary for {{borrower_name}} ({{industry_name}}, {{case_type}}, "
     "{{relationship}}). Summarise the credit ask, key strengths and key risks from "
     "{{doc:audited_financials}} and {{doc:sanction_letter}}. End with a recommendation "
     "placeholder for the analyst.",
     ["audited_financials", "sanction_letter"], False, True),
    ("borrower_profile", "Borrower Profile & Group Structure",
     "Describe {{borrower_name}}'s business, promoters and group structure based on "
     "{{doc:audited_financials}} and {{doc:previous_cam}}.",
     ["audited_financials", "previous_cam"], False, False),
    ("industry_analysis", "Industry & Market Analysis",
     "Assess the {{industry_name}} industry outlook as it affects {{borrower_name}}. "
     "Evaluate the borrower against these industry KPIs:\n{{industry_kpis}}",
     ["audited_financials"], True, False),
    ("financial_analysis", "Financial Analysis",
     "Analyse the financial performance and position of {{borrower_name}} using "
     "{{doc:audited_financials}} and {{doc:provisional_financials}}. Cover growth, "
     "profitability, leverage and liquidity. Apply relevant KPIs:\n{{industry_kpis}}",
     ["audited_financials", "provisional_financials"], True, False),
    ("banking_conduct", "Banking Conduct & Account Behaviour",
     "Review account conduct of {{borrower_name}} from {{doc:bank_statements}} and "
     "{{doc:credit_bureau_report}}: utilisation, overdrawals, bureau flags.",
     ["bank_statements", "credit_bureau_report"], False, False),
    ("facility_structure", "Facility Structure, Security & Covenants",
     "Set out the proposed facilities, security and covenants for {{borrower_name}} "
     "from {{doc:sanction_letter}}.",
     ["sanction_letter"], False, False),
    ("risk_mitigants", "Risk Analysis & Mitigants",
     "Identify the key credit risks for {{borrower_name}} and available mitigants, "
     "grounded on all mapped sources. Apply relevant KPIs:\n{{industry_kpis}}",
     ["audited_financials", "bank_statements", "sanction_letter"], True, False),
]

TEMPLATE = {
    "name": "Corporate CAM — ETB", "segment": "corporate", "relationship": "etb",
    "template_instructions": ("House style: UK English, amounts in INR crore, neutral "
                              "analytical register. Where a section references prior-year "
                              "data, state the financial year explicitly."),
    "sections": [
        {"order": 1, "section_code": "exec_summary", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "250 words", "fixed_format": True},
        {"order": 2, "section_code": "borrower_profile", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "300 words", "fixed_format": False},
        {"order": 3, "section_code": "industry_analysis", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "300 words", "fixed_format": False},
        {"order": 4, "section_code": "financial_analysis", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "400 words", "fixed_format": False},
        {"order": 5, "section_code": "banking_conduct", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "250 words", "fixed_format": False},
        {"order": 6, "section_code": "facility_structure", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "300 words", "fixed_format": True},
        {"order": 7, "section_code": "risk_mitigants", "mandatory": True,
         "include_if_doctype": None, "length_guidance": "300 words", "fixed_format": False},
        {"order": 8, "section_code": "project_review", "mandatory": False,
         "include_if_doctype": "project_report", "length_guidance": "", "fixed_format": False},
    ],
    "required_doc_types": ["audited_financials", "bank_statements", "sanction_letter"],
}

PROJECT_PROMPT = ("project_review", "Project Review",
                  "Review the capital project of {{borrower_name}} from {{doc:project_report}}: "
                  "cost, means of finance, DSCR and implementation status.",
                  ["project_report"], False, False)


def seed(client: httpx.Client) -> dict:
    admin1 = login(client, "admin1")
    admin2 = login(client, "admin2")
    versions: dict[str, int] = {}

    for code, (name, synonyms, keywords) in DOCTYPES.items():
        versions[f"doctype:{code}"] = publish(client, admin1, admin2, "doctypes", code, {
            "code": code, "name": name, "description": name, "synonyms": synonyms,
            "keywords": keywords, "active": True})

    for sector_code, sector_name, code, name in INDUSTRIES:
        versions[f"industry:{code}"] = publish(client, admin1, admin2, "industries", code, {
            "sector_code": sector_code, "sector_name": sector_name,
            "industry_code": code, "industry_name": name})

    for industry_code, kpis in KPI_SETS.items():
        versions[f"kpi:{industry_code}"] = publish(client, admin1, admin2, "kpi-sets",
                                                   industry_code,
                                                   {"industry_code": industry_code, "kpis": kpis})

    versions["prompt:global"] = publish(client, admin1, admin2, "prompts",
                                        "global_standing_rules", GLOBAL_RULES)
    for rule_key, rule_text in AGENT_RULES.items():
        versions[f"prompt:{rule_key}"] = publish(client, admin1, admin2, "prompts", rule_key, {
            "section_code": rule_key,
            "section_name": rule_key.replace("_", " ").title(),
            "scope": "global", "source_doc_types": [], "uses_industry_kpis": False,
            "prompt_text": rule_text})
    # sections that benefit from external market/news context when a connector
    # is enabled (no effect while connectors stay off — the default)
    external_sections = {"industry_analysis", "risk_mitigants"}
    for code, name, text, sources, uses_kpis, _fixed in [*SECTION_PROMPTS, PROJECT_PROMPT]:
        versions[f"prompt:{code}"] = publish(client, admin1, admin2, "prompts", code, {
            "section_code": code, "section_name": name, "scope": "section",
            "prompt_text": text, "source_doc_types": sources,
            "uses_industry_kpis": uses_kpis,
            "uses_external_context": code in external_sections})

    versions["template:corp-etb"] = publish(client, admin1, admin2, "templates",
                                            "corp-etb", TEMPLATE)
    return versions


if __name__ == "__main__":
    with httpx.Client(timeout=30.0) as client:
        try:
            versions = seed(client)
        except httpx.ConnectError:
            sys.exit("gateway not reachable — start the stack first: python scripts/run_stack.py")
    print(f"seeded {len(versions)} published master versions (maker-checker exercised on each)")
