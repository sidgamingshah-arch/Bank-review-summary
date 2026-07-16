# ADR-0001 — Monorepo, one Python distribution, eight deployables

**Status:** accepted · **Date:** 2026-07-16

## Context
NFR-02 mandates independently deployable microservices (master-config,
document, tagging, orchestration, GenAI gateway, output, audit, auth adapter).
A distribution per service (8 × pyproject + shared-lib versioning) maximises
isolation but taxes a v1 team with dependency-matrix upkeep.

## Decision
One installable distribution (`cam-platform`) containing `cam.common` plus one
package per service, each with its own FastAPI app, its own database session
factory and its own tables. Deployment isolation happens at runtime: every
service is a separate process/container (`SERVICE_MODULE` env in the shared
image), owns its data, and speaks to peers only through the gateway.

## Consequences
* One `pip install`, one test run, no shared-lib version skew during v1.
* Service boundaries stay enforceable (no cross-service imports beyond
  `cam.common`; contracts.md is the only coupling surface), so splitting into
  per-service images/distributions later is mechanical.
* A change to `cam.common` redeploys all services — acceptable at this stage,
  revisit when teams split.
