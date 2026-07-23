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
| `CAM_CONNECTOR_NEWS_URL`, `CAM_CONNECTOR_SEARCH_URL` | connector endpoints |
| `CAM_CONNECTOR_API_KEY_ENV` / `CAM_CONNECTOR_API_KEY` | connector key |
| `CAM_JWT_SECRET` | token signing secret — set a real value for any live run |

The provider is built once per process, so changing any `CAM_GENAI_*` value
requires a stack restart.
