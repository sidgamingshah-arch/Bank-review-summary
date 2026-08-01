# ADR-0007 — Opik as the system-of-record for section prompts

**Status:** accepted · **Date:** 2026-08-01

## Context
Section prompts (the prompt masters keyed by `section_code`) are the highest-churn,
highest-value configuration in the platform and benefit from a dedicated prompt
registry with first-class versioning and observability. The direction is to store
the **prompt master** in Opik (Comet's prompt library). The platform, however, has
hard requirements that Opik does not itself provide: maker-checker governance, a
per-run reproducibility snapshot, a hash-chained audit trail, and — critically —
the ability to run **fully offline** (dev, tests, the demo, air-gapped installs)
with zero external dependencies.

## Decision
Opik is the **system-of-record for section-prompt content**; master-config remains
the **governance and reproducibility** layer around it.

- **Scope:** section prompts only. The global standing rules and the four agent
  rules stay in master-config (they are not section content and are small,
  governance-first artifacts). Predicate: `is_section_prompt(key)`.
- **Write-through on publish:** when a section-prompt version is approved (the
  maker-checker boundary), its text is written to the Opik prompt library
  (`create_prompt`) and the returned commit is stamped on the master version's
  **`provenance.opik`** — deliberately *outside* the validated business `payload`,
  so schema validation and the export/import bundle round-trip are unaffected.
- **Read-through at generation:** `resolve` reads the authoritative text back from
  Opik by `(name, commit)`; the resolved bundle (and the per-run snapshot) carries
  it, so reproducibility holds.
- **Backends:** `opik` (a real deployment, via the optional `opik` SDK, lazily
  imported) when `CAM_OPIK_ENABLED`; otherwise a **local stand-in** — `publish`
  returns a deterministic content-hash ref and `fetch` returns `None`, so callers
  use the master-config snapshot. Same pattern as mock-LLM / local-RAG / local-blob.
- **Fail-open, always:** any Opik error, a missing SDK, or an unreachable server
  never blocks a publish or a run — the platform degrades to the snapshot. The
  Opik API key is referenced by env-var **name** only (NFR-06).

## Consequences
- Prompts are managed and versioned in Opik in production, with its observability;
  the bank keeps maker-checker, per-run snapshots and the audit chain unchanged.
- The DB retains a content snapshot (not pure delegation). This is intentional: it
  preserves offline operation and run reproducibility, and is the fallback when
  Opik is unavailable. Pure delegation (no snapshot) was rejected for breaking both.
- Config: `CAM_OPIK_*`; status at `GET /api/masters/opik/status`; extra
  `pip install -e .[opik]`.
