# ADR-0005 — Single GenAI egress with layered assembly, mock-first providers

**Status:** accepted · **Date:** 2026-07-16

## Context
NFR-10 (LLM access only via a gateway service through APIM), NFR-09 (document
content must not act as instructions), FR-A02 (three-layer prompt hierarchy),
FR-D04 (no fabrication), FR-B03 (preferences are style-only). Dev/CI cannot
depend on a live model endpoint.

## Decision
`cam.services.genai` is the only component that talks to a model. It owns:
* **Assembly** — house standing rules ⊕ global prompt-master rules ⊕ template
  instructions ⊕ style directives (system side); resolved section prompt ⊕
  sanitised `<document>` data blocks (user side). Fixed-format sections drop
  user preferences entirely.
* **Injection defence** — document text cannot close its wrapper tag
  (escaped), is length-capped, and standing rule 3 pins its role as data.
* **Trace check** — numeric/date tokens in output not present in grounding or
  case context are returned as `untraceable_numbers`, surfaced in run records
  and the data-gap trailer. A deterministic backstop, not a substitute for
  analyst review.
* **Providers** — `mock` (deterministic composer that only reuses grounded
  figures; default for dev/CI/demo) and `anthropic` (official SDK, default
  model `claude-opus-4-8`, sampling params withheld on models that reject
  them, refusals surfaced as flagged failures). Bedrock/Vertex are provider
  additions behind the same interface, mirroring the ASM prototype's pattern.

## Consequences
* The whole platform — including the acceptance suite — runs and is verified
  offline; switching to the bank's endpoint is configuration.
* Per-call token usage flows back to runs for the cost dashboard (FR-F06).
* Prompt caching / batching optimisations live in one place when needed.
