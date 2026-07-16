# CAM Studio — Frontend

React single-page front-end for the AI-Assisted Credit Assessment Memo (CAM) Generation
Platform. Built strictly against `../docs/contracts.md` (v1) — every call goes through the
gateway at `/api` (proxied to `http://localhost:8080` in dev).

## Run

```bash
npm install
npm run dev        # Vite dev server on http://localhost:5173, /api proxied to :8080
npm run build      # tsc -b && vite build  → dist/
npm run preview    # serve the production build
```

Demo users (password `Demo#2026`): `admin1`, `admin2` (business_admin), `itadmin`
(it_admin), `analyst1`, `analyst2` (analyst), `reviewer1` (reviewer), `auditor1` (auditor).

Stack: Vite + React 18 + TypeScript, react-router-dom v6, marked + DOMPurify for markdown.
No UI kit, no state library — hand-rolled CSS design tokens in `src/styles.css`.

## Route map → BRD screens

| Route | Screen | Roles |
|---|---|---|
| `/login` | SSO stand-in login (posts `/api/auth/token`) | all |
| `/cases` | Case list + "New case" modal | analyst (create), reviewer (read) |
| `/cases/:id` | Case workspace: document intake (drag-drop, sequential one-file-per-request uploads, repository pull), tagging review (confirm / retag / period / remove / add), completeness check + generation launcher with preference override | analyst, reviewer |
| `/runs/:id` | Live generation progress (1.5 s poll), per-section status/attempts/tokens, retry failed sections, untraceable-number warnings, gaps panel, audit-friendly run record | analyst, reviewer |
| `/cams/:id` | CAM output workspace: section nav, markdown view / autosaving editor with optimistic locking (409 → reload), named versions, history + diff, section regeneration, conversational AI panel with attachment upload and accept/reject suggestions, finalise + DOCX/PDF export, AI-ASSISTED DRAFT watermark | analyst, reviewer |
| `/admin/masters/:tab` | Masters workbench: Prompts / Templates / Doc Types / Industries / KPI Sets / Settings. Versioned maker-checker lifecycle (draft → in_review → published/rejected, rollback), type-specific payload forms, version diff, prompt sandbox test, KPI CSV bulk upload + export, tagging threshold setting | business_admin |
| `/admin/users` | User administration (create, edit roles, activate/deactivate) | it_admin |
| `/audit` | Audit trail: filters, pagination, expandable event detail, CSV/JSON export, hash-chain verification, CAM lineage lookup | auditor, business_admin |
| `/preferences` | Personal output preferences; business_admin also edits the org default | all |

## Architecture

```
src/
  api/client.ts      fetch wrapper: bearer token (localStorage 'cam.token'),
                     error envelope → ApiError, 401 → clear token + /login,
                     get/post/put/patch/del/postForm/download helpers
  api/types.ts       TypeScript mirror of every contract shape
  api/uploads.ts     one-file-per-request case document upload helper
  auth/              AuthContext (GET /api/auth/me bootstrap), route guards
  layout/Shell.tsx   top bar + role-filtered left nav
  components/        StatusChip, Modal, ConfirmDialog, DataTable, Markdown,
                     DiffView, Toast, Spinner, EmptyState, ChipsInput, PreferenceForm
  pages/             one folder per feature area (cases, runs, cams, masters,
                     audit, admin, preferences)
```

## Contract assumptions (conservative readings)

- `GET /api/runs?case_id=` (`RunSummary`) and `GET /api/cams?case_id=` (`CamSummary`)
  entry shapes are not pinned by the contract; treated as optional subsets of
  `Run`/`Cam` and rendered defensively (used only for the "Runs & CAMs" case card).
- `GET /api/audit/lineage/cam/{id}` shape is not pinned; the lineage view renders a
  `run`/`run_record` object and an `events` array when present, otherwise falls back
  to pretty-printed JSON.
- Multipart file field name is `file` (matches the documented `kpi-sets/bulk` field).
- `Tag.confirmed` is not in the documented Tag shape but is accepted by PATCH; typed
  as optional and used only to decide whether to show the "Confirm" button.
- `PUT /api/masters/settings` sends the full settings object back with
  `tagging_confidence_threshold` replaced, passing unknown keys through unchanged.
- Prompt items with `scope: "global"` derive the item key `global_standing_rules`
  per the contract note; all other keys derive from their payload identity field
  (templates take an explicit slug).
- Chat history for "whole document" scope calls `GET /api/cams/{id}/chat` without
  `section_id` (assumed to return all messages).
