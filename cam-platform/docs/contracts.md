# CAM Platform — Service & API Contracts (v1)

Single source of truth for every service boundary. Backend and front-end are built
against THIS document; deviations must be reconciled here first.

All routes are exposed **through the gateway** (APIM stand-in) at `http://localhost:8080`
with prefix `/api`. Services never call each other point-to-point; they call the gateway
(NFR-04). The front-end talks only to the gateway.

## Ports (local dev)

| Component | Port | Uvicorn app |
|---|---|---|
| gateway (APIM stand-in) | 8080 | `cam.gateway.main:app` |
| auth-adapter | 8101 | `cam.services.auth.main:app` |
| master-config | 8102 | `cam.services.master_config.main:app` |
| document | 8103 | `cam.services.document.main:app` |
| tagging | 8104 | `cam.services.tagging.main:app` |
| orchestration | 8105 | `cam.services.orchestration.main:app` |
| genai-gateway | 8106 | `cam.services.genai.main:app` |
| output | 8107 | `cam.services.output.main:app` |
| audit | 8108 | `cam.services.audit.main:app` |
| frontend (Vite dev) | 5173 | proxies `/api` → 8080 |

Gateway routing table (path prefix → service):
`/api/auth` → auth · `/api/masters` → master-config · `/api/cases`, `/api/documents` → document ·
`/api/tagging` → tagging · `/api/runs` → orchestration · `/api/genai` → genai ·
`/api/cams` → output · `/api/audit` → audit.

## Conventions

- IDs: UUID4 strings. Timestamps: ISO-8601 UTC (`2026-07-16T10:00:00Z`).
- Errors: HTTP status + envelope `{"error": {"code": "<slug>", "message": "<human>", "details": <any|null>}}`.
  Codes used: `unauthorized`, `forbidden`, `not_found`, `validation_error`, `conflict`,
  `maker_checker_violation`, `quarantined`, `not_published`, `rate_limited`.
- Auth: `Authorization: Bearer <JWT>` on every call. HS256, secret `CAM_JWT_SECRET`.
  User claims: `sub`, `username`, `display_name`, `roles: [..]`, `typ: "user"`, `exp`, `iat`.
  Service-to-service: same secret, `typ: "service"`, `svc: "<service-name>"`, `roles: ["service"]`.
- Correlation: gateway injects `X-Correlation-ID` (uuid) if absent; all services propagate
  it on outbound calls and include it in audit events.
- Content is **markdown** everywhere a section body appears.

## Roles → capabilities (BRD §4)

| capability | business_admin | it_admin | analyst | reviewer | auditor | service |
|---|---|---|---|---|---|---|
| `masters:read` | ✔ | – | ✔ (published only) | ✔ | ✔ | ✔ |
| `masters:draft` / `masters:submit` | ✔ | – | – | – | – | – |
| `masters:approve` (checker ≠ maker) | ✔ | – | – | – | – | – |
| `masters:settings`, `org_defaults:set` | ✔ | – | – | – | – | – |
| `case:create`, `docs:manage` | – | – | ✔ | – | – | – |
| `case:read` | – | – | own | all | all (read) | ✔ |
| `generate:run`, `cam:edit`, `cam:converse`, `cam:finalise`, `cam:download` | – | – | ✔ | ✔ | – | – |
| `audit:read` | domain=masters | – | own cases | case-level | all | – |
| `users:admin`, `env:config` | – | ✔ | – | – | – | – |
| `prefs:own` | ✔ | ✔ | ✔ | ✔ | ✔ | – |
| `internal:write` (audit ingest, cam create, genai call) | – | – | – | – | – | ✔ |

`genai` accepts only `typ=service` tokens (NFR-10: no direct model calls from front-end).

---

## 1. auth-adapter (`/api/auth`)

Dev stand-in for bank IdP SSO (prod: OIDC/SAML — swap this service only).

- `POST /api/auth/token` `{username, password}` → `200 {access_token, token_type:"bearer", expires_in, user: User}`
- `GET /api/auth/me` → `User`
- `GET /api/auth/users` (it_admin) → `[User]`; `POST /api/auth/users` (it_admin) `{username, display_name, email, roles, password}` → `User`; `PATCH /api/auth/users/{id}` `{roles?, active?}` → `User`
- `GET /api/auth/preferences` → `PreferenceProfile` (own; falls back to org default)
- `PUT /api/auth/preferences` `PreferenceProfileInput` → `PreferenceProfile`
- `GET|PUT /api/auth/preferences/org-default` (PUT: `org_defaults:set`) → `PreferenceProfile`

```
User               = {id, username, display_name, email, roles: [str], active: bool}
PreferenceProfileInput = {tonality: "crisp"|"narrative", structure_bias: "bullets"|"paragraphs",
                          table_usage: "auto"|"prefer"|"avoid", length: "concise"|"standard"|"detailed"}
PreferenceProfile  = PreferenceProfileInput + {scope: "user"|"org_default", updated_at}
```

Seeded users (password `Demo#2026` for all): `admin1`, `admin2` (business_admin),
`itadmin` (it_admin), `analyst1`, `analyst2` (analyst), `reviewer1` (reviewer), `auditor1` (auditor).

---

## 2. master-config (`/api/masters`)

Generic versioned-master engine, maker-checker controlled (FR-A03/A19). Master types
(`{mtype}` path segment): `prompts`, `templates`, `doctypes`, `industries`, `kpi-sets`.

Version lifecycle: `draft → in_review → published | rejected`; publishing retires the
previously published version (`retired`). Only `published` versions resolve at runtime.
Approve enforces checker ≠ maker (`maker_checker_violation` otherwise).

- `GET /api/masters/{mtype}` → `[ItemSummary]` `{key, item_id, latest_version: int|null,
  latest_status: str|null, published_version: int|null, updated_at}`
- `POST /api/masters/{mtype}` `{key, payload, change_note}` → `Item` (creates item + draft v1)
- `GET /api/masters/{mtype}/{key}` → `{key, item_id, versions: [VersionMeta], published_version}`
- `GET /api/masters/{mtype}/{key}/versions/{no}` → `Version` (meta + payload)
- `POST /api/masters/{mtype}/{key}/versions` `{payload, change_note, effective_from?}` → new draft `Version`
- `POST .../versions/{no}/submit` → `Version(in_review)`
- `POST .../versions/{no}/approve` → `Version(published)` · `POST .../versions/{no}/reject {reason}` → `rejected`
- `POST .../versions/{no}/rollback` → new **draft** cloned from version `{no}` (then submit/approve as usual)
- `GET /api/masters/{mtype}/{key}/diff?from=1&to=3` → `{diff: "<unified diff of canonical payload JSON>"}`
- `GET /api/masters/resolve/template/{key}` → `ResolvedTemplate` (below) — 404/`not_published` if any part unpublished
- `POST /api/masters/kpi-sets/bulk` (multipart `file`=CSV) → `{created:[], updated:[], errors:[{row, message}]}` (drafts only)
- `GET /api/masters/kpi-sets/export.csv` → CSV of published KPI sets
- `POST /api/masters/prompts/{key}/sandbox-test` `{sample_docs: [{doctype_code, text}], placeholders?: {}}` → `{content, model, usage}` (FR-A05; calls genai with the DRAFT latest version)
- `GET /api/masters/settings` → `{tagging_confidence_threshold: float, ...}` · `PUT /api/masters/settings` (business_admin)
- `GET /api/masters/published/doctypes` → `[doctype payload]` (all currently-published doc types;
  used by tagging/document services — avoids N+1 version lookups)

```
VersionMeta = {version_no, status, created_by, created_at, submitted_by?, approved_by?,
               approved_at?, effective_from?, change_note}
Version     = VersionMeta + {payload}
```

### Payload schemas (validated at save; placeholder syntax validated per FR-A04)

```
prompt   : {section_code, section_name, scope: "section"|"global", prompt_text,
            source_doc_types: [doctype_code], uses_industry_kpis: bool,
            rendering_hints?: str, model_overrides?: {model?, temperature?, max_tokens?}}
template : {name, segment: "corporate"|"fi"|"project_finance", relationship: "etb"|"ntb",
            template_instructions: str,
            sections: [{order:int, section_code, mandatory: bool,
                        include_if_doctype?: doctype_code|null, length_guidance?: str,
                        fixed_format: bool}],
            required_doc_types: [doctype_code]}
doctype  : {code, name, description, synonyms: [str], keywords: [str], active: bool,
            file_constraints?: {formats:[".pdf",...], max_mb:int, max_count:int},
            feeds_sections?: [section_code]}
industry : {sector_code, sector_name, industry_code, industry_name}
kpi-set  : {industry_code, kpis: [{code, name, definition, unit, polarity: "higher_better"|"lower_better",
            benchmark?: str|null, sections: [section_code]}]}
```

Item `key`: prompts → `section_code` (global rules use key `global_standing_rules`);
templates → slug; doctypes → `code`; industries → `industry_code`; kpi-sets → `industry_code`.

Allowed placeholders in prompt/template text (FR-A04): `{{borrower_name}}`, `{{case_type}}`,
`{{relationship}}`, `{{industry_name}}`, `{{industry_kpis}}`, `{{doc:<doctype_code>}}`,
`{{today}}`. Unknown placeholders or unknown doctype codes → `422 validation_error` with a
list of offending tokens.

```
ResolvedTemplate = {template_key, template_version, template: <payload>,
                    global_rules: {prompt_key, version, prompt_text}|null,
                    sections: [{order, section_code, mandatory, include_if_doctype, length_guidance,
                                fixed_format, prompt: {key, version, payload}}],
                    doctype_master_versions: {code: version_no},
                    settings: {tagging_confidence_threshold}}
```

---

## 3. document (`/api/cases`, `/api/documents`)

Owns cases, document intake (VAF pipeline), tags, completeness. Binaries and text
extracts go to blob storage (`.data/blobs`, `.data/extracts`) — never the DB (NFR-03).

- `POST /api/cases` `{borrower_name, segment, relationship, industry_code}` → `Case`
- `GET /api/cases` → `[Case]` (analyst: own; reviewer/auditor/service: all)
- `GET /api/cases/{id}` → `Case`
- `POST /api/cases/{id}/documents` — **multipart, exactly ONE file per request** (FR-C02/NFR-07;
  FE fans a multi-select out into sequential single-file requests). Optional form fields:
  `origin` (`upload`|`chat`|`repository`, default `upload`), `period_label`.
  Pipeline (synchronous): validate (ext/size) → AV scan (EICAR stub) → sha256 + duplicate check
  → store blob → extract text → auto-tag via tagging service.
  → `201 Document` (status `ready`|`no_text`) or `201 Document` with status `quarantined`
  (the record is created so the user sees the reason; quarantined docs are never usable).
- `POST /api/cases/{id}/pull` `{source: "repository", external_ref: str}` → `Document`
  (repository-pull stand-in; loads a fixture blob through the SAME pipeline — FR-C03)
- `GET /api/cases/{id}/documents` → `[Document]`
- `GET /api/documents/{id}` → `Document` · `GET /api/documents/{id}/text` (service or case-scoped user) → `{text}`
- `DELETE /api/documents/{id}` → 204
- `POST /api/documents/{id}/tags` `{doctype_code, period_label?, seq_order?, page_range?}` → `Tag`
- `PATCH /api/documents/{id}/tags/{tag_id}` `{doctype_code?, period_label?, seq_order?, confirmed?: bool}` → `Tag`
- `DELETE /api/documents/{id}/tags/{tag_id}` → 204
- `GET /api/cases/{id}/completeness?template_key=<key>` →
  `{required: [doctype_code], present: [doctype_code], missing: [doctype_code], can_proceed: true}`

```
Case     = {id, borrower_name, segment, relationship, industry_code, status: "open"|"generating"|"finalised",
            created_by, created_at}
Document = {id, case_id, filename, content_type, size_bytes, sha256, status:
            "quarantined"|"ready"|"no_text", quarantine_reason: str|null, origin,
            duplicate_of: doc_id|null, extraction: "ok"|"empty"|"unsupported",
            uploaded_by, uploaded_at, tags: [Tag]}
Tag      = {id, doctype_code, confidence: float|null, source: "auto"|"user",
            needs_review: bool, period_label: str|null, seq_order: int|null, page_range: str|null}
```

Accepted formats v1: `.pdf .docx .xlsx .csv .txt` · max 25 MB (or doctype `file_constraints`).

---

## 4. tagging (`/api/tagging`) — internal (service tokens; admins may call for testing)

- `POST /api/tagging/classify` `{filename, text}` →
  `{candidates: [{doctype_code, confidence: 0..1}], threshold, llm_consulted: bool,
    best: {doctype_code, confidence, needs_review, method: "keyword"|"llm", rationale?}}`
  Two-pass classification: (1) explainable name/synonym/keyword scoring vs filename
  (strong) + extracted text (weaker), normalised 0..1; (2) when pass 1 finds nothing or
  only a below-threshold guess, an LLM fallback via `POST /api/genai/classify`
  `{filename, text, doctypes}` → `{code|null, confidence, rationale, model, usage}`
  (model must pick from the catalogue or null; fail-open — intake never blocks on it).
  `needs_review = confidence < threshold` (threshold from master settings).

---

## 5. orchestration (`/api/runs`)

- `POST /api/runs` `{case_id, template_key, preference_override?: PreferenceProfileInput, proceed_with_gaps?: bool}`
  → `202 Run` (snapshots ResolvedTemplate versions, KPI set version, preference profile,
  computes gaps; refuses `conflict` if gaps exist and `proceed_with_gaps` is not true)
- `GET /api/runs/{id}` → `Run` (poll target) · `GET /api/runs?case_id=` → `[RunSummary]`
- `POST /api/runs/{id}/sections/{section_code}/retry` → 202 (failed → queued; FR-D02)
- `POST /api/runs/{id}/sections/{section_code}/regenerate` → 202 (complete → new generation;
  on success POSTs the new content to output service as a new section version, source `regeneration`)
- `GET /api/runs/usage/summary` (business_admin/auditor) → `{runs, sections, tokens_in, tokens_out, retries, regenerations}`

```
Run = {id, case_id, template_key, status: "queued"|"running"|"complete"|"partial"|"failed",
       cam_id: str|null, created_by, created_at, correlation_id,
       applied_preferences: PreferenceProfileInput + {source: "user"|"override"|"org_default"},
       master_versions: {template: int, prompts: {section_code: int}, kpi_set: int|null,
                         doctypes: {code:int}, global_rules: int|null},
       model_identity: str, gaps: [{doctype_code, reason}],
       sections: [{section_code, name, order, status: "queued"|"running"|"complete"|"failed"|"skipped",
                   attempts, error: str|null, tokens_in, tokens_out, untraceable: [str]}]}
```

Worker: DB-backed queue (`SectionJob` rows, `SELECT ... FOR UPDATE SKIP LOCKED` semantics),
in-process asyncio workers (`GEN_WORKER_CONCURRENCY`, default 2). Per-user active-run cap
`MAX_ACTIVE_RUNS_PER_USER` (default 2) → `429 rate_limited` (FR-D07).
On run completion (all sections terminal): POST CAM to output service with every completed
section + a **data-gap trailer** section (`section_code: "_gaps"`) listing missing inputs and
untraceable figures (FR-D05); then set `run.cam_id`.

---

## 6. genai-gateway (`/api/genai`) — service tokens ONLY (NFR-10)

- `POST /api/genai/generate`
  ```
  {mode: "section",
   layers: {global_rules: str|null, template_instructions: str|null, section_prompt: str},
   placeholders: {borrower_name, case_type, relationship, industry_name, today, industry_kpis: str},
   grounding_docs: [{doctype_code, label, text}],        # ONLY this section's mapped docs (FR-D03)
   preferences: PreferenceProfileInput|null,             # null when fixed_format (FR-B04)
   fixed_format: bool, length_guidance: str|null,
   model_overrides?: {model?, temperature?, max_tokens?}}
  ```
  → `{content, model, usage: {input_tokens, output_tokens}, untraceable_numbers: [str]}`
  Assembly: system = standing no-fabrication + injection-defence rules (house) ⊕ global_rules
  ⊕ template_instructions ⊕ style directives from preferences; user = section_prompt with
  placeholders resolved ⊕ grounding docs wrapped in `<document doctype=.. label=..>` data
  blocks (content sanitised: role markers stripped — NFR-09).
  Post-check: numeric/date tokens in `content` not present in grounding → `untraceable_numbers` (FR-D04).
- `POST /api/genai/edit`
  `{current_content, instruction, scope: "document"|"section", grounding_docs: [..],
    preferences: PreferenceProfileInput|null}` → `{proposed_content, rationale, model, usage}`
- Providers: `LLM_PROVIDER=mock` (default; deterministic, offline) | `anthropic`
  (`ANTHROPIC_API_KEY`, model `GENAI_MODEL`). Provider/model identity returned on every call.

---

## 7. output (`/api/cams`)

- `POST /api/cams` (service) `{case_id, run_id, title, template_key, created_by,`
  `sections: [{section_code, name, order, content, fixed_format, generated: bool}]}` → `Cam`
  (`created_by` = the analyst who launched the run; drives own-case RBAC scoping.
  Section `_gaps` is the data-gap trailer: rendered/exported but never editable.)
- `GET /api/cams?case_id=` → `[CamSummary]` · `GET /api/cams/{id}` → `Cam` (sections with current content)
- `PUT /api/cams/{id}/sections/{section_id}`
  `{content, version_name?: str, base_version_no: int}` → `SectionVersion` (autosave = unnamed;
  `409 conflict` if `base_version_no` ≠ current — FR-E09 optimistic locking)
- `GET /api/cams/{id}/sections/{section_id}/versions` → `[SectionVersionMeta]`
- `GET /api/cams/{id}/sections/{section_id}/diff?from=1&to=3` → `{diff}`
- `POST /api/cams/{id}/sections/{section_id}/versions` (service; regeneration path)
  `{content, source: "regeneration"}` → `SectionVersion`
- `POST /api/cams/{id}/chat` `{scope: "document"|"section", section_id?: str, message,`
  `attached_document_ids?: [doc_id]}` → `{message: ChatMessage, reply: ChatMessage, suggestion: Suggestion|null}`
  (fetches attached docs' text from document service as extra grounding — FR-E05;
  AI reply that proposes content ALWAYS lands as a pending Suggestion, never applied — FR-E06)
- `GET /api/cams/{id}/chat?section_id=` → `[ChatMessage]`
- `GET /api/cams/{id}/suggestions?status=pending` → `[Suggestion]`
- `POST /api/cams/{id}/suggestions/{sid}/accept` → `{suggestion, new_version}` · `POST .../reject {reason?}` → `{suggestion}`
- `POST /api/cams/{id}/finalise` → `Cam(status="final")` (FR-E08 watermark drops from exports)
- `GET /api/cams/{id}/export.docx` · `GET /api/cams/{id}/export.pdf` → binary download
  (draft exports carry "AI-ASSISTED DRAFT" watermark header; gap trailer always rendered)

```
Cam            = {id, case_id, run_id, title, template_key, status: "draft"|"final",
                  finalised_by?, finalised_at?, created_at,
                  sections: [{id, section_code, name, order, fixed_format, current_version_no,
                              content, updated_at}]}
SectionVersion = {section_id, version_no, name: str|null, source: "generated"|"manual"|
                  "chat_suggestion"|"regeneration", created_by, created_at}
Suggestion     = {id, cam_id, section_id, status: "pending"|"accepted"|"rejected",
                  instruction, proposed_content, diff, created_at, decided_by?, decided_at?}
ChatMessage    = {id, cam_id, scope, section_id: str|null, role: "user"|"assistant", content,
                  attached_document_ids: [..], created_at}
```

---

## 8. audit (`/api/audit`)

Append-only, hash-chained (tamper-evident): `hash = sha256(prev_hash + canonical(event))`.

- `POST /api/audit/events` (any authenticated service/user; normally emitted via the common
  audit client) `{action, entity_type, entity_id, case_id?, run_id?, cam_id?, detail?: {}}`
  → `201 {id, seq}` — actor/role/correlation taken from token + header, ts server-set.
- `GET /api/audit/events?entity_type=&entity_id=&case_id=&action=&actor=&limit=&offset=`
  → `{events: [AuditEvent], total}` (RBAC scoping per matrix)
- `GET /api/audit/lineage/cam/{cam_id}` → full lineage: run record (master versions, model,
  preferences, doc hashes) + every edit/suggestion/chat/finalise event (FR-F01/F02, AC-4)
- `GET /api/audit/export?format=csv|json&case_id=` → download
- `GET /api/audit/verify-chain` → `{intact: bool, checked: int, first_break_seq: int|null}`
- `GET /api/audit/mrm/sample?n=5` (auditor/business_admin) → `{runs: [run_id]}` (FR-F05)

`AuditEvent = {id, seq, ts, actor, actor_roles, action, entity_type, entity_id,
               case_id?, run_id?, cam_id?, correlation_id, detail, prev_hash, hash}`

Canonical action strings: `master.created|master.version_created|master.submitted|
master.approved|master.rejected|master.rolled_back|settings.updated|case.created|
document.uploaded|document.pulled|document.quarantined|document.deleted|tag.auto_applied|
tag.added|tag.changed|tag.removed|run.started|run.section_completed|run.section_failed|
run.section_retried|run.section_regenerated|run.completed|cam.created|cam.section_edited|
cam.chat_message|cam.suggestion_created|cam.suggestion_accepted|cam.suggestion_rejected|
cam.finalised|cam.exported|user.login|user.created|user.updated|prefs.updated`

---

## Common library (`cam.common`) — what services import

- `config.py` — `Settings` (env-driven): `gateway_url`, `service_name`, `jwt_secret`, `db_url`
  (default `sqlite:///.data/<service>.db`), `data_dir`, provider settings.
- `db.py` — `make_engine(url)`, `SessionLocal`, `Base`, `init_db(app_models)`.
  Models must stay SQLite+Postgres compatible (String/Text/Integer/Float/Boolean/DateTime/JSON).
- `security.py` — `create_user_token(user)`, `create_service_token(svc)`, `decode_token`,
  FastAPI deps: `current_principal`, `require(*capabilities)`, `require_service`.
  `rbac.py` — the capability matrix above as data.
- `audit.py` — `emit(action, entity_type, entity_id, *, principal, correlation_id, case_id=None,
  run_id=None, cam_id=None, detail=None)` → POSTs to `{gateway}/api/audit/events` with a
  service token; failures logged, never crash business flow (fail-open, WARN).
- `correlation.py` — ASGI middleware: read/generate `X-Correlation-ID`, stash in contextvar;
  `get_correlation_id()`.
- `http.py` — `gateway_client()` httpx.Client with service token + correlation header baked in.
- `placeholders.py` — `find_placeholders(text)`, `validate_placeholders(text, doctype_codes)`,
  `resolve_placeholders(text, mapping)`.
- `errors.py` — `ApiError(status, code, message, details)` + FastAPI handler producing the envelope.
- `markdownish.py` — tiny markdown helpers shared by exports (split paragraphs/bullets/tables).

Every service `main.py`: FastAPI app + correlation middleware + error handler +
`GET /healthz` → `{status:"ok", service}`.
