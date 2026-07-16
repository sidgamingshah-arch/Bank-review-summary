# ADR-0003 — One versioned-master engine for all five masters

**Status:** accepted · **Date:** 2026-07-16

## Context
The BRD defines four admin masters (prompt, industry KPI, template, document
type — we model industry taxonomy and KPI sets as two types, so five) that all
need the same controls: draft → review → published lifecycle, maker-checker
approval, history, diff, rollback, effective-dating (FR-A03/A06/A19, §4).

## Decision
A generic `MasterItem`/`MasterVersion` engine (`master_config/engine.py`) with
one lifecycle implementation, plus per-type Pydantic payload validators that
carry the referential rules (placeholders against doc types, template sections
against prompt keys, KPI sets against the taxonomy — FR-A04/A14). Runtime
resolution reads published versions only, honouring `effective_from`.

## Consequences
* Maker-checker, rollback and diff are provably identical across masters —
  one implementation, one test suite, one audit vocabulary (`master.*`).
* Publishing retires the predecessor; rollback clones an old payload into a
  NEW draft that walks the normal approval path — history is never rewritten.
* Adding a future master (e.g. covenant library) is a payload schema + one
  entry in `MTYPES`.
