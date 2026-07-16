# ADR-0004 — SQLite-dev/PostgreSQL-prod, blob storage, DB-backed job queue

**Status:** accepted · **Date:** 2026-07-16

## Context
NFR-03: PostgreSQL as system-of-record; binaries in the enterprise repository/
object store, never the DB. NFR-02: async messaging for generation jobs. Dev
and CI must run with zero external infrastructure.

## Decision
* **Relational**: SQLAlchemy 2 models restricted to types portable across
  SQLite and PostgreSQL. Dev/tests run per-service SQLite files; compose runs
  PostgreSQL 16 (shared database in dev-compose, strict per-service table
  ownership; per-service schemas/databases at the bank).
* **Binaries**: documents and extracts go to `blob_dir`/`extract_dir` paths
  behind the settings layer — the integration point for the enterprise
  repository/object store. Nothing binary touches the DB.
* **Queue**: `SectionJob` rows form the generation queue; workers claim with a
  serialised claim (SQLite) that maps to `FOR UPDATE SKIP LOCKED` semantics on
  PostgreSQL. In-process asyncio workers (configurable concurrency) execute
  jobs; the orchestration container scales horizontally.

## Consequences
* Every FR is testable offline; CI runs the full acceptance walkthrough.
* The queue gives exactly the BRD-required behaviours cheaply: per-section
  status, individual retry, regeneration — and its transactional-outbox shape
  swaps for the bank's MQ/Kafka without changing job semantics.
* Alembic migrations are a pre-production step (models are the schema source
  of truth in v1; `create_all` on startup).
