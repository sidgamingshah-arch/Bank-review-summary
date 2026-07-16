# Bank ASM Review-Note Generator

LLM **extraction + reasoning** pipeline that turns ASM / stock-audit report PDFs (plus a
tracker / base-data sheet, sanction letter, insurance docs, or a previous-quarter note)
into a structured **ASM Review Note** Word document — generated directly, no manual keying.

Built on Claude (Opus 4.8) via the Anthropic SDK, with a pluggable provider layer
(Anthropic API direct, AWS Bedrock, or Google Vertex AI).

> Fields that the template marks "to be fed from tracker/base data" or "manually entered"
> are **also auto-extracted when present** in the uploaded documents. Anything genuinely
> not found is written into the `.docx` as a clearly-marked `[To be entered by L1]`
> placeholder for someone to complete afterward.

## How it works

```
Upload (ASM PDF[s] + tracker/base Excel/CSV + sanction/insurance/prev-quarter docs)
   │
   ▼  ingest/loader.py        PDFs -> base64 document blocks; Excel/CSV -> text tables; + verify corpus
   ▼  llm/extract.py          per-section structured call (messages.parse), docs cached as prefix
   ▼  llm/reason.py           deterministic normalisation (e.g. tidy deviation %)
   ▼  verify.py               substring-verify each evidence quote vs the source text; QC report
   ▼  pipeline.py             assemble -> ReviewNote (Pydantic)
   ▼  render/docx_renderer.py -> .docx in the template layout (placeholders for blanks)
   ▼  download
```

- **Per-section structured extraction.** Each of the 13 sections is one focused
  `messages.parse` call whose `output_format` is that section's Pydantic model, so output
  is typed and reliable. The source documents are sent as a cached prefix (`cache_control`)
  so they are processed once and reused across sections.
- **Provenance in the schema.** Every value is wrapped in `Field[T]`
  (`value, found, confidence, source_document, page, evidence_quote`). The native
  *citations* feature can't be combined with structured output, so provenance lives in the
  schema and `verify.py` substring-checks each `evidence_quote` against the text extracted
  from the sources. Unverifiable values are kept but downgraded to low confidence and listed
  in a QC report (the `.docx` itself stays clean). If the sources have no extractable text
  (e.g. a scanned PDF read via vision), verification is skipped rather than over-flagging.

## Project layout

```
backend/
  asm_review/            # reusable, UI-agnostic core
    config.py            # settings (provider, model, limits) from env / .env
    llm/provider.py      # client selection + capability flags
    llm/prompts.py       # system prompt + per-section instructions
    llm/extract.py       # per-section structured calls + section loop
    llm/reason.py        # deterministic post-extraction normalisation
    ingest/loader.py     # files -> Claude content blocks + verification text
    schema/              # Field[T] + section models + ReviewNote
    verify.py            # provenance verification + QC report
    render/docx_renderer.py  # ReviewNote -> .docx
    pipeline.py          # orchestration
  app/                   # FastAPI: upload -> background job -> poll -> download
    main.py, jobs.py, static/index.html
  tests/                 # offline tests (fake client; no API key needed)
```

## Setup

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then paste your API key into .env
```

**API keys live in one file: `backend/.env`** (copied from `.env.example`). It is
git-ignored and loaded automatically on startup into the environment, so every provider
SDK picks up the keys. You can instead name it `secrets.env`, or point `ASM_ENV_FILE` at
any path. Configure the provider in that file:

| `LLM_PROVIDER` | Needs | Notes |
|---|---|---|
| `anthropic` (default) | `ANTHROPIC_API_KEY` | Full feature set. |
| `bedrock` | AWS credentials + `AWS_REGION` | Model id is auto-prefixed `anthropic.`. |
| `vertex` | `VERTEX_PROJECT_ID` (+ ADC) | `VERTEX_REGION` default `global`. |

## Run

```bash
cd backend && . .venv/bin/activate
uvicorn app.main:app --reload
# open http://127.0.0.1:8000
```

Upload the source files, wait for the background job (it runs ~13 sequential model
calls — a few minutes), then download the generated Word note. The status page also
shows a QC summary (fields populated, source-verified, left as placeholders, unverified).

### Use the core directly (no web app)

```python
from asm_review.pipeline import run_pipeline
result = run_pipeline(["report.pdf", "tracker.xlsx"], "review_note.docx")
print(result.qc.as_dict())   # QC summary
```

## Testing

```bash
cd backend && . .venv/bin/activate && pytest
```

Tests use a fake client and run fully offline (no API key, no network).

## Limitations / next steps

- **Layout is modelled on the pasted template.** Exact labels, column widths, fonts, and
  numbering should be aligned against a real `.docx` template, and prompts re-tuned against
  a real ASM PDF — expect one refinement pass.
- **Deviation % is not auto-computed** from a guessed formula (the basis varies by bank);
  it is taken from the source/model and only its formatting is normalised.
- **Job store is in-memory** (single process). For durable / multi-worker deployment, swap
  `app/jobs.py` for a real queue + datastore; the pipeline is stateless.
- Do not log document contents / PII; uploaded files and outputs live under `DATA_DIR`.
