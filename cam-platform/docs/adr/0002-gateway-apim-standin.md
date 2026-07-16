# ADR-0002 — In-repo gateway as the APIM stand-in

**Status:** accepted · **Date:** 2026-07-16

## Context
NFR-04 requires all exposure through the enterprise APIM: front-end→backend,
POP→POD, and repository calls, with authN/Z, throttling and logging policies.
The bank's APIM is not available in dev, but the topology must match the
target or "works locally, fails behind APIM" becomes the norm.

## Decision
Ship `cam.gateway`: a reverse proxy enforcing the same policy set — bearer
token required (except login), GenAI plane closed to non-service identities
(NFR-10), per-principal throttling, correlation-id minting, structured access
logs, env-driven route table. All services call each other through it; nothing
listens for point-to-point traffic in any environment.

## Consequences
* Dev/prod parity: swapping in real APIM is configuration (route policies),
  not code. The e2e suite exercises the gateway path on every run.
* The stand-in is not a hardened gateway (no WAF, no mTLS termination); it
  exists to keep the call graph honest, and its policies document exactly what
  the bank's APIM must enforce.
