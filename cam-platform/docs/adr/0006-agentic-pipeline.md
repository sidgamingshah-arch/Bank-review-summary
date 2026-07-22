# ADR-0006 — Agentic generation pipeline (extraction / summarisation / materiality / consistency)

**Status:** accepted · **Date:** 2026-07-22

## Context
A single drafting call cannot separately guarantee grounding quality,
material completeness and internal consistency — and gives MRM a single
opaque step to review. The product direction requires named, individually
auditable agents, with document tagging equally AI-based.

## Decision
Each section runs a four-agent pipeline, conducted deterministically by the
orchestration worker (the conductor is code, not a model — retries, ordering
and persistence stay deterministic), with every model role executed through
the single GenAI egress (NFR-10):

1. **Extraction agent** (`/api/genai/extract`) — structured, source-attributed
   facts (item/value/unit/source/quote) from the section's mapped documents only.
2. **Summarisation agent** (`/api/genai/generate`) — drafts the section with
   the extracted facts as primary grounding (style prefs, fixed-format lock,
   three-layer prompt hierarchy unchanged).
3. **Materiality check agent** (`/api/genai/materiality`) — verdict on material
   coverage (KPI framework, large exposures, covenant headroom). A failing
   verdict feeds a **bounded revision loop** (`agent_revision_limit`, default 1)
   back through the summariser.
4. **Consistency check agent** (`/api/genai/consistency`) — draft vs facts and
   vs other completed sections' figures (cross-section digest), same bounded
   revision semantics. The deterministic numeric trace check remains the final
   backstop.

Governance: each agent's standing rules are **prompt-master entries**
(reserved global keys `agent_*_rules`) — maker-checker controlled, versioned,
snapshotted per run (`master_versions.agent_rules`), carried by the export
bundle. Check agents can be toggled and the revision budget tuned in master
settings. Every agent call lands in the section's `agent_trace` (model, tokens,
verdict) and unresolved verdicts are disclosed in the data-gap trailer — never
silently accepted. An unparseable check reply becomes an explicit "no usable
verdict" outcome; the pipeline never invents a pass.

Tagging follows the same principle: `tagging_mode = ai_first` (default) makes
LLM classification primary with the keyword scorer as corroboration
(disagreement ⇒ review flag) and fallback when the model is unavailable;
`keyword_first` / `keyword_only` remain for cost-constrained deployments.

## Consequences
* ~4–6 model calls per section instead of 1 — bounded by the revision limit
  and the check toggles; token spend per agent is visible in the trace and the
  usage summary.
* The mock provider implements every role deterministically, so the full
  agentic behaviour (including revision loops) is exercised offline in CI.
* Adding an agent (e.g. a covenant-verification role) is: system prompt +
  mock behaviour + one conductor step + a reserved rules key.
