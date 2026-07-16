# CAM Platform — AI-Assisted Credit Assessment Memo Generation

Configurable, AI-driven platform that turns borrower documents into
first-draft Credit Assessment Memos: business-administered masters (prompts,
industry KPIs, templates, document types) under maker-checker control drive
section-by-section generation; analysts review, edit, converse with the AI
(human-in-the-loop tracked suggestions) and finalise to DOCX/PDF — with a
tamper-evident audit trail reconstructing every CAM's full lineage.

Built against the v0.1 BRD. Requirement-by-requirement status:
**[docs/traceability.md](docs/traceability.md)** · architecture:
**[docs/architecture.md](docs/architecture.md)** · API contracts:
**[docs/contracts.md](docs/contracts.md)** · decisions: **[docs/adr/](docs/adr/)**.

## Quickstart (no external dependencies)

```bash
make install     # venv + backend deps
make test        # 64 unit/service tests
make e2e         # full BRD §9 acceptance walkthrough: starts all 9 services,
                 # seeds masters via maker-checker, uploads/tags docs (incl. a
                 # quarantined EICAR file), generates a CAM, edits + chats with
                 # in-chat upload, finalises, exports DOCX/PDF, verifies lineage
```

Interactive demo:

```bash
make stack       # gateway :8080 + 8 services (SQLite, mock LLM, worker on)
make seed        # publish the demo master configuration (admin1 + admin2)
make frontend    # or `make frontend-dev` for the SPA on :5173
```

Sign in at http://localhost:5173 — seeded users (password `Demo#2026`, dev IdP
stub only): `admin1`/`admin2` (business admins, maker-checker pair), `itadmin`,
`analyst1`/`analyst2`, `reviewer1`, `auditor1`.

Containerised (PostgreSQL): `docker compose up --build` (gateway on :8080).

## Using a real model

The default GenAI provider is `mock` — deterministic, offline, and honest with
the no-fabrication trace check. To use the bank-approved endpoint:

```bash
CAM_LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... make stack
# model defaults to claude-opus-4-8; override with CAM_GENAI_MODEL
```

Bedrock/Vertex are provider additions behind the same interface (ADR-0005).

## Layout

```
src/cam/
  common/            settings · SQLAlchemy plumbing · JWT+RBAC (BRD §4 matrix)
                     · audit client · correlation id · placeholders · markdown
  gateway/           APIM stand-in: routing, authN, throttling, GenAI lockout
  services/
    auth/            dev IdP stub (swap for bank OIDC) · users · preferences
    master_config/   5 versioned masters, maker-checker engine, CSV bulk, resolve
    document/        cases · VAF intake (quarantine) · tags · completeness
    tagging/         synonym/keyword classifier with confidence + threshold
    orchestration/   runs · version snapshots · DB-backed section queue · workers
    genai/           layered prompt assembly · injection defence · providers ·
                     untraceable-figure check
    output/          CAM working copy · versions/diffs · chat + tracked
                     suggestions · finalise · DOCX/PDF export (draft watermark)
    audit/           append-only hash-chained events · lineage · export · MRM sample
frontend/            React SPA (login, masters workbench, case/document/tag
                     screens, run progress, CAM workspace with chat, audit)
scripts/             run_stack.py · seed_demo.py · e2e_demo.py (AC-1…AC-5)
tests/               64 tests across all services
```

## Relationship to `../backend`

`backend/` is the earlier single-purpose ASM review-note prototype. This
platform generalises its proven ideas — pluggable LLM providers, provenance
verification, section-wise structured drafting — into the configurable,
multi-user, audited product the BRD describes. The prototype remains as-is; a
future template pack can reproduce its ASM note inside this platform.
