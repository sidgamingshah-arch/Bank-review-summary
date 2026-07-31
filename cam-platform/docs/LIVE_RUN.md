# Running a live case on your own LLM endpoint

This walks through generating a real Credit Assessment Memo end-to-end against
**your own OpenAI-compatible LLM endpoint**, with your own prompt library, and
with the search / negative-news connectors either on or off.

Everything below runs against the local stack (`scripts/run_stack.py`); the same
environment variables apply to the container topology in `docker-compose.yml`.

---

## 1. Point the platform at your endpoint

The GenAI gateway is the single LLM egress (NFR-10). Select the `openai`
provider and give it your endpoint — all via environment (secrets never live in
the DB or the front-end, NFR-06):

```bash
export CAM_LLM_PROVIDER=openai
export CAM_GENAI_BASE_URL=https://llm.your-bank.internal/v1   # include the /v1 (or equivalent) prefix
export CAM_GENAI_MODEL=your-model-id
export CAM_GENAI_API_KEY=...          # the key value; read at start, never logged
# optional:
#   CAM_GENAI_API_KEY_ENV=CAM_GENAI_API_KEY   # name of the env var above (default shown)
#   CAM_GENAI_AUTH_SCHEME=Bearer              # "" for a raw key header
#   CAM_GENAI_TEMPERATURE=0.0
#   CAM_GENAI_MAX_TOKENS=2000
#   CAM_GENAI_TIMEOUT_SECONDS=120
export CAM_JWT_SECRET=$(openssl rand -hex 32)   # don't ship the dev default
```

Any OpenAI-compatible `/v1/chat/completions` endpoint works — vLLM, LiteLLM,
Azure OpenAI, Ollama, or a bank-hosted gateway.

### Confirm the endpoint before starting the stack

```bash
python scripts/llm_smoke.py
# provider=openai model=your-model-id base_url=https://.../v1
# OK  model=...  usage={'input_tokens': .., 'output_tokens': ..}
```

If you don't have an endpoint handy but want to see the live path work, run the
bundled fake endpoint in another terminal and point at it:

```bash
python scripts/fake_openai_server.py --port 8909
export CAM_LLM_PROVIDER=openai CAM_GENAI_BASE_URL=http://127.0.0.1:8909/v1 CAM_GENAI_MODEL=fake-1
```

## 2. Start the stack

```bash
python scripts/run_stack.py       # gateway on :8080; Ctrl-C to stop
# frontend (separate terminal): cd frontend && npm run dev
```

## 3. Load your master configuration

You configure the prompt library, templates, doc types, industries and KPI sets
**before** the run. Two ways:

- **Bulk upload (recommended for a fresh environment).** In the UI:
  *Masters → Bulk import → Download template*, fill one row per entry across the
  sheets, then *Upload filled workbook*. Or via CLI:

  ```bash
  python scripts/masters_bundle.py template masters.xlsx      # download blank template
  # ...fill it in...
  python scripts/masters_bundle.py bulk-upload masters.xlsx --user admin1
  ```

- **Carry config from another environment:** `masters_bundle.py export/import`.

Everything lands as **drafts** — nothing takes effect until it is submitted and
approved by a *different* admin (maker-checker). Approve via *Masters → (tab) →
version → Submit / Approve*, or script it (see `scripts/seed_demo.py` for the
maker→checker pattern). A published template needs every referenced section
prompt and doc type published too, or resolution returns `409 not_published`.

> To start from the worked demo library instead of your own, run
> `python scripts/seed_demo.py` against the running stack.

## 4. (Optional) External connectors

The client-provided **negative-news** and **web/search** connectors are off by
default — runs work on the uploaded documents alone. To use them:

1. Set the endpoint URLs at deployment: `CAM_CONNECTOR_NEWS_URL`,
   `CAM_CONNECTOR_SEARCH_URL` (and `CAM_CONNECTOR_API_KEY` if they need a key).
   With a toggle **on** but no URL set, a clearly-marked mock feed is used so
   the path is demonstrable.
2. Turn them on in *Masters → Settings → External connectors*.

Only sections whose prompt sets **`uses_external_context`** consult a connector;
their fetched, source-labelled text is added to that section's **extraction**
grounding (sanitised for prompt-injection like any source), and the CAM's
*Data Gaps & Disclosures* trailer lists every external source consulted. The
fetch is fail-open: a connector outage never blocks or fails a run.

## 4b. (Optional) Large documents — retrieval (RAG)

For long documents — a 300-page annual report, say — enable retrieval so each
section is grounded on the **most relevant passages** rather than the first slice
of full text (which would miss the financials buried deep in the file).

1. Configure an **embedding endpoint**. In *Masters → Settings → LLM endpoint →
   Embedding endpoint*, set the provider to `openai-compatible`, give it an
   embedding model (e.g. `text-embedding-3-small`) and, if different from chat,
   a base URL; the key comes from the env var you name (never entered in the UI).
   Embeddings are independent of chat, so chat can stay on Anthropic while
   embeddings run on an OpenAI-compatible endpoint. Offline, `mock` works with no
   network. (Env equivalents: `CAM_GENAI_EMBED_PROVIDER`, `CAM_GENAI_EMBED_MODEL`,
   `CAM_GENAI_EMBED_BASE_URL`, `CAM_GENAI_EMBED_API_KEY_ENV`.)
2. Turn on **Large-document retrieval (RAG)** in *Masters → Settings* and set
   *passages per document (top-K)*.
3. Upload documents **after** enabling RAG — they are chunked and embedded at
   intake. For documents uploaded earlier, `POST /api/documents/{id}/reindex`
   indexes them on demand.

At generation time each section retrieves its top-K passages per mapped document;
anything not retrieved falls back to full-text grounding, so a run never loses a
source. The run trace's per-section **retrieval** step shows exactly which
passages grounded each section. RAG is fail-open end-to-end: if the embedding
endpoint is unavailable, generation degrades to full-text grounding. Also raise
`CAM_MAX_EXTRACT_CHARS` (default ~2,000,000 ≈ 650 pages) if your documents are
larger, so retrieval can reach the whole file.

## 4c. (Optional) Azure resources

The platform runs fully on open-source/local defaults, but each piece can be
pointed at Azure independently — config-gated, so mixing is fine.

- **Azure OpenAI (chat + embeddings + reasoning).** Set `CAM_LLM_PROVIDER=azure`
  and/or `CAM_GENAI_EMBED_PROVIDER=azure`, `CAM_AZURE_OPENAI_ENDPOINT`, and the
  key in the env var named by `CAM_AZURE_OPENAI_API_KEY_ENV`. The chat/embedding
  **deployment names** are `CAM_GENAI_MODEL` / `CAM_GENAI_EMBED_MODEL`. For an
  o-series reasoning deployment set `CAM_AZURE_OPENAI_REASONING=true`. In the UI:
  *Masters → Settings → LLM endpoint*, pick provider `azure` and fill the Azure
  card (endpoint, api-version, deployment names, reasoning flag).
- **Azure AI Search (retrieval index).** `CAM_RETRIEVAL_BACKEND=azure_search`,
  `CAM_AZURE_SEARCH_ENDPOINT`, key env var, `CAM_AZURE_SEARCH_INDEX`. The index
  is auto-created on first upsert; set `CAM_GENAI_EMBED_DIM` to your embedding
  model's dimension (e.g. 1536 for text-embedding-3-small, 3072 for -large).
  Chunks are pushed at intake and queried (vector / keyword / hybrid) per section.
- **Azure Blob Storage (documents).** `CAM_BLOB_BACKEND=azure` +
  `AZURE_BLOB_CONNECTION_STRING` (or `CAM_AZURE_BLOB_ACCOUNT_URL` for managed
  identity). Install the extra: `pip install "cam-platform[azure]"`. Binaries and
  text extracts move to the configured containers.

Keys live only in env/vault (NFR-06) — the UI shows *configured / not set*, never
values. Validate everything you enabled from inside your environment:

```bash
python scripts/azure_check.py   # probes only the Azure services you turned on
```

## 4d. (Optional) Generation tuning — concurrency, consistency, interlinking

Three runtime levers in *Masters → Settings* (no restart needed):

- **Concurrency (sections drafted in parallel).** *Generation performance →
  Concurrency* sets how many sections generate at once — the biggest lever on how
  fast a memo completes. It is clamped to the worker pool spawned at deployment
  (`CAM_WORKER_POOL_SIZE`, default 8); raise the pool to allow higher live
  concurrency. Higher = faster, but more concurrent load on your LLM endpoint —
  keep it under the endpoint's rate/concurrency limit.
- **Run queue — how many memos at once** (`max_concurrent_runs`, default 4). When
  several analysts submit runs together, every request is accepted and queued; at
  most this many generate concurrently (FIFO) and the rest start automatically as
  slots free, with a per-user fairness cap (`CAM_MAX_ACTIVE_RUNS_PER_USER`) so one
  person's burst can't monopolise the queue. Raise it for more throughput, lower it
  to protect a rate-limited endpoint. When a run finishes, its creator gets an
  in-app notification (the bell in the header) linking straight to the memo.
- **Email notifications** (`email_notifications`, default on). In addition to the
  in-app bell, the run's creator is emailed on completion with a deep link back to
  the memo. Sending needs SMTP configured at deploy time; until then the mailer just
  logs the message, so the toggle is safe to leave on. To send real email set:
  `CAM_SMTP_HOST`, `CAM_SMTP_PORT` (587 STARTTLS / 465 with `CAM_SMTP_SSL=true`),
  `CAM_SMTP_USERNAME`, and put the password in the env var named by
  `CAM_SMTP_PASSWORD_ENV` (default `CAM_SMTP_PASSWORD`) — the value is read at send
  time and never stored on Settings or logged (NFR-06). `CAM_SMTP_FROM` sets the
  sender; `CAM_APP_BASE_URL` is the base for the deep link (the gateway origin).
- **When the consistency agent runs** (`consistency_scope`). *Assurance agents →
  When the consistency agent runs*:
  - **After all sections** (default) — one memo-level pass sees every section
    together once they're all drafted and re-drafts **only** the sections it flags
    (bounded by the revision limit). Best cross-section coherence; adds one
    reconcile pass per run. Fail-open: a reconcile error still finalises the memo.
  - **Per section** — checked as each section is written (sees only the siblings
    finished so far). Lower latency, no extra pass.
- **Section interlinking** (`depends_on` / `depends_on_all`). Declare, per template
  section, which other sections' output feeds it (they finish first and their
  drafted text is added to its grounding). The classic case is an **executive
  summary** that `depends_on_all` — displayed first but generated last, consuming
  every other section. Set it in the bulk template's `template_sections` sheet
  (`depends_on` = pipe-separated section codes, `depends_on_all` = TRUE) or the JSON
  bundle. The dependency graph must be acyclic (rejected at save otherwise).

## 5. Run a case

In the UI (as an analyst): create a case, upload the borrower's documents (they
are validated, virus-scanned and AI-tagged), resolve any tag conflicts, then
**Generate** against your template. The run screen shows each section moving
through the agent pipeline (extraction → summarisation → materiality →
consistency); open the CAM workspace when it completes to review, edit inline,
and use the conversational copilot.

## 6. What to check

- Run record shows `model_identity` = your model id (not `mock-...`).
- Per-section token usage is non-zero and matches your endpoint's accounting.
- The *Settings* page's **LLM endpoint** card shows your provider/model/base URL
  and `API key: configured`.
- With a connector on, opted-in sections show the external source in the CAM's
  gap-disclosure trailer.

## Reference — environment variables

| Variable | Purpose |
|---|---|
| `CAM_LLM_PROVIDER` | `mock` \| `anthropic` \| `openai` |
| `CAM_GENAI_BASE_URL` | OpenAI-compatible base URL (incl. version prefix) |
| `CAM_GENAI_MODEL` | model id |
| `CAM_GENAI_API_KEY_ENV` / `CAM_GENAI_API_KEY` | env-var name holding the key / its value |
| `CAM_GENAI_AUTH_SCHEME` | Authorization scheme (`Bearer`, or `""`) |
| `CAM_GENAI_TEMPERATURE`, `CAM_GENAI_MAX_TOKENS`, `CAM_GENAI_TIMEOUT_SECONDS` | sampling / limits |
| `CAM_GENAI_EMBED_PROVIDER` | `mock` \| `openai` (embedding backend, for RAG) |
| `CAM_GENAI_EMBED_MODEL` / `CAM_GENAI_EMBED_BASE_URL` | embedding model id / base URL (empty → chat base URL) |
| `CAM_GENAI_EMBED_API_KEY_ENV` | env-var name holding the embedding key (value never stored) |
| `CAM_RAG_ENABLED`, `CAM_RAG_TOP_K` | retrieval defaults (also runtime settings) |
| `CAM_RAG_CHUNK_SIZE`, `CAM_RAG_CHUNK_OVERLAP`, `CAM_MAX_EXTRACT_CHARS` | chunking / extract cap |
| `CAM_CONNECTOR_NEWS_URL`, `CAM_CONNECTOR_SEARCH_URL` | connector endpoints |
| `CAM_CONNECTOR_API_KEY_ENV` / `CAM_CONNECTOR_API_KEY` | connector key |
| `CAM_JWT_SECRET` | token signing secret — set a real value for any live run |

The provider is built once per process, so changing any `CAM_GENAI_*` value
requires a stack restart.
