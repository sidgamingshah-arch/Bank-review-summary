# BRD → Implementation Traceability (v1)

Status legend: **✔ Implemented** · **◐ Partial** (works, with a documented
narrowing) · **▷ Deferred** (v1 scope decision, integration point in place).
"Verified" points at the automated proof: `tests/…` (unit/service) or `e2e`
(scripts/e2e_demo.py — runs the full stack, gated in CI).

## 6.1-A Prompt Master

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-A01 | M | ✔ | CRUD + section metadata, mapped doc types, KPI flag, rendering hints — `master_config` payloads, admin UI Prompts tab | tests/test_master_config.py |
| FR-A02 | M | ✔ | house → global (`global_standing_rules`) → template → section layering — `genai/assembly.py` | tests/test_genai.py, e2e |
| FR-A03 | M | ✔ | draft→in_review→published, maker≠checker, history, diff, one-click rollback — `master_config/engine.py` | tests + e2e AC-1 |
| FR-A04 | M | ✔ | `{{…}}` framework validated at save vs doc-type catalogue — `common/placeholders.py` | tests/test_master_config.py |
| FR-A05 | S | ✔ | sandbox test-run of the LATEST (draft) version with sample docs — `POST /prompts/{key}/sandbox-test` | tests/test_master_config.py |
| FR-A06 | S | ✔ | `effective_from` honoured by published-version resolution | engine (`published_version`) |
| FR-A07 | M | ✔ | run snapshots every prompt version id (`run.master_versions.prompts`) | tests/test_orchestration.py, e2e AC-4 |
| FR-A08 | C | ✔ | `model_overrides` (model/temperature/token cap) per prompt, applied by provider | tests/test_genai.py |

## 6.1-B Industry KPI Master

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-A09 | M | ✔ | two-level taxonomy (sector→industry) as `industries` master | e2e seed |
| FR-A10 | M | ✔ | KPI records: name/definition/unit/polarity/benchmark/sections | tests/test_master_config.py |
| FR-A11 | M | ✔ | section-scoped `{{industry_kpis}}` injection at runtime — `worker.render_kpi_block` | tests/test_orchestration.py |
| FR-A12 | S | ✔ | CSV upload with row-level validation report + export | tests/test_master_config.py |
| FR-A13 | S | ✔ | `run.master_versions.kpi_set` stored per run | e2e AC-4 |

## 6.1-C Template Master

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-A14 | M | ✔ | ordered sections, each bound to a prompt-master key (validated) | tests/test_master_config.py |
| FR-A15 | M | ✔ | mandatory / include-if-document / length guidance / fixed-format lock | tests/test_orchestration.py (skip + fixed-format) |
| FR-A16 | M | ◐ | segment×relationship dimensions on the template; variants via new key with copied payload (admin UI form) — no dedicated clone endpoint | — |
| FR-A17 | M | ✔ | `required_doc_types` drives the completeness check | tests/test_document_service.py, e2e AC-2 |
| FR-A18 | S | ◐ | section skeleton visible in the admin form and version view; no dedicated preview render | — |
| FR-A19 | M | ✔ | same lifecycle engine as prompts (ADR-0003) | tests + e2e AC-1 |

## 6.1-D Document-Type Master

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-A20 | M | ✔ | code-value master with synonyms/keywords/active | tests, e2e |
| FR-A21 | S | ✔ | `feeds_sections` on doc types + `source_doc_types` on prompts | resolution path |
| FR-A22 | C | ◐ | `file_constraints` stored per type; intake enforces the global format/size limits, per-type overrides not yet wired into VAF | — |

## 6.2 Output Preferences

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-B01 | M | ✔ | tonality/structure/tables/length profile per user | tests, e2e |
| FR-B02 | M | ✔ | per-run override; applied profile stored on the run with its source | tests/test_orchestration.py |
| FR-B03 | M | ✔ | style-only guardrail in assembly + trace check on figures | tests/test_genai.py |
| FR-B04 | M | ✔ | fixed-format sections drop preferences entirely | tests/test_orchestration.py |
| FR-B05 | C | ✔ | org-default profile, admin-settable | smoke + auth service |

## 6.3 Ingestion, Tagging, Data Pull

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-C01 | M | ✔ | drag-drop multi-file with per-file progress (SPA) | FE build |
| FR-C02 | M | ✔ | strictly one file per backend request; VAF validate→scan→quarantine with visible reason | tests/test_document_service.py, e2e AC-2 |
| FR-C03 | M | ✔ | repository pull through the same pipeline (`/cases/{id}/pull`); fixture-backed stand-in for the enterprise repository API | e2e AC-2 |
| FR-C04 | M | ✔ | two-pass: explainable keyword/synonym scorer, then LLM classification via the GenAI gateway when the name match reveals nothing or only a below-threshold guess (fail-open; method recorded on the tag audit) | tests/test_tagging_service.py, tests/test_genai.py |
| FR-C05 | M | ✔ | many docs per type with period labels + ordering, used in grounding labels | e2e |
| FR-C06 | M | ✔ | tag view/add/change/confirm/remove, all audited (`tag.*`) | tests, e2e |
| FR-C07 | S | ✔ | sha256 duplicate detection — warn and proceed (`duplicate_of`) | tests/test_document_service.py |
| FR-C08 | S | ◐ | text extraction (pdf/docx/xlsx/csv/txt) with per-file status; scanned PDFs surface as `no_text` — OCR engine is a documented integration point | tests |
| FR-C09 | M | ✔ | completeness vs template; run refuses unless `proceed_with_gaps`; gaps disclosed in trailer | tests/test_orchestration.py, e2e |
| FR-C10 | C | ◐ | multiple tags per document incl. `page_range` field (split-tagging data model + API); simple UI | tests |

## 6.4 Generation & Orchestration

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-D01 | M | ✔ | resolve → snapshot → async job per section | tests, e2e |
| FR-D02 | M | ✔ | per-section queued/running/complete/failed/skipped + individual retry | tests/test_orchestration.py |
| FR-D03 | M | ✔ | grounding strictly from the section's mapped doc types | tests/test_orchestration.py |
| FR-D04 | M | ✔ | standing no-fabrication rules + deterministic numeric/date trace check; untraceable figures flagged, never dropped silently. Heuristic scope (numbers/dates; names not machine-checked) documented in ADR-0005 | tests/test_genai.py |
| FR-D05 | M | ✔ | `_gaps` trailer: missing docs, skipped/failed sections, flagged figures | e2e AC-2/AC-3 |
| FR-D06 | S | ✔ | regenerate → new version of that section only (source `regeneration`) | tests/test_orchestration.py |
| FR-D07 | C | ✔ | per-user active-run cap (429) + configurable worker concurrency | tests/test_orchestration.py |

## 6.5 Output Workspace

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-E01 | M | ✔ | section-wise rendering per profile, navigation rail | FE |
| FR-E02 | M | ✔ | DOCX (python-docx) + PDF (fpdf2) faithful to section structure/tables | tests/test_output_service.py, e2e |
| FR-E03 | M | ◐ | markdown editor with autosave coalescing, named versions, diff compare, history — WYSIWYG rich-text is the v1.1 upgrade | tests, e2e AC-3 |
| FR-E04 | M | ✔ | chat panel scoped to document or section; rewrite/shorten/table/re-analyse | tests, e2e AC-3 |
| FR-E05 | M | ✔ | in-chat upload → same VAF+tagging pipeline → grounding for the edit | e2e AC-3 |
| FR-E06 | M | ✔ | AI output lands as pending tracked suggestions; explicit accept/reject only path into the document; finalise blocked while pending | tests, e2e AC-3 |
| FR-E07 | M | ✔ | chat messages + suggestion decisions + diffs audited | e2e AC-4 |
| FR-E08 | S | ✔ | AI-ASSISTED DRAFT watermark until finalised (header + banner), drops after | tests/test_output_service.py |
| FR-E09 | S | ✔ | optimistic locking (`base_version_no` → 409) | tests, e2e AC-3 |
| FR-E10 | C | ▷ | one-pager/summary variant — not built; template model can carry it later | — |

## 6.6 Audit & Governance

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| FR-F01 | M | ✔ | immutable run record: user, time, template/prompt/KPI/doctype versions, model identity, preferences, document hashes | e2e AC-4 |
| FR-F02 | M | ✔ | full manual + conversational edit trail, exportable per CAM (`/audit/export?cam_id=`) | e2e AC-4 |
| FR-F03 | M | ✔ | before/after on master changes + settings (`master.*`, `settings.updated`) | tests |
| FR-F04 | S | ✔ | read-only auditor role with search + CSV/JSON export | e2e AC-4 |
| FR-F05 | S | ◐ | masters + runs are versioned/approved artifacts; `GET /audit/mrm/sample` for periodic output review; the formal MRM committee workflow is organisational | — |
| FR-F06 | C | ◐ | `/runs/usage/summary` (runs, tokens, retries, regenerations, failures) + tag-correction events for the feedback loop; dashboard UI deferred | tests |

## 7 Non-functional

| ID | Pri | Status | Implementation / notes | Verified |
|---|---|---|---|---|
| NFR-01 | M | ✔ | React SPA (Vite+TS), desktop-first, zero-error build | FE build (CI) |
| NFR-02 | M | ✔ | 8 services + gateway, async DB-backed generation queue (ADR-0001/0004) | e2e |
| NFR-03 | M | ✔ | PostgreSQL in compose (SQLite dev); binaries/extracts in blob storage, never DB | ADR-0004 |
| NFR-04 | M | ✔ | all calls (FE→BE and service→service) traverse the gateway/APIM stand-in with authN, throttling, logging (ADR-0002) | e2e |
| NFR-05 | M | ✔ | BRD §4 matrix as data, enforced everywhere; SSO adapter swappable | tests (denials), e2e |
| NFR-06 | M | ✔ | no secrets client-side; short-lived JWTs; env/vault secrets; no credential material in payloads/logs (demo passwords are the dev IdP stub, called out in README) | e2e AC-5 |
| NFR-07 | M | ✔ | bulk-upload UX, strictly sequential single-file VAF submission, quarantine + notification | e2e AC-2 |
| NFR-08 | M | ◐ | at-rest/in-transit encryption is deployment-layer (TLS at APIM, encrypted volumes); app side: no doc content in audit/logs, extracts capped. Bank policy wiring pending | — |
| NFR-09 | M | ✔ | document content is inert data: sanitised wrappers + standing rules | tests/test_genai.py |
| NFR-10 | M | ✔ | GenAI reachable by service identities only; gateway blocks user tokens at the edge | tests + e2e AC-5 |
| NFR-11 | S | ◐ | correlation id minted at gateway, spans every hop, stored on runs + audit events; gateway access logs. Central log/metric shipping is deployment config | e2e |
| NFR-12 | S | ✔ | containerised (compose), horizontally scalable workers | compose config |
| NFR-13 | M | ▷ | performance/availability/RTO-RPO targets pending bank input (as flagged in the BRD) | — |

## 9 Acceptance criteria

All five ACs are automated in `scripts/e2e_demo.py` (16 checks) and run in CI:
masters+rollback under maker-checker · upload→tag→generate→edit→finalise→
download in one session · conversational edit with in-chat upload as a tracked
suggestion · full lineage reconstruction + intact hash chain · zero
client-visible credentials with the model plane closed to end users.
