# AI-Assisted Credit Assessment Memo (CAM) Generation Platform

Configurable, AI-driven platform that generates first-draft Credit Assessment
Memos from borrower documents and data — analysts spend their time on judgment
and structuring rather than drafting, while every prompt, KPI, template and
document type stays in business-administered masters under maker-checker
control, and every CAM's full lineage is reconstructable from a tamper-evident
audit trail.

**The platform lives in [`cam-platform/`](cam-platform/) — start at its
[README](cam-platform/README.md).**

```bash
cd cam-platform
make install     # venv + dependencies
make test        # 64 unit/service tests
make e2e         # automated BRD §9 acceptance walkthrough (all 9 services)
```

| I want to… | Go to |
|---|---|
| Run / demo the platform | [cam-platform/README.md](cam-platform/README.md) |
| See the solution architecture | [cam-platform/docs/architecture.md](cam-platform/docs/architecture.md) |
| Check requirement-by-requirement status vs the BRD | [cam-platform/docs/traceability.md](cam-platform/docs/traceability.md) |
| Read the service/API contracts | [cam-platform/docs/contracts.md](cam-platform/docs/contracts.md) |
| Understand key decisions | [cam-platform/docs/adr/](cam-platform/docs/adr/) |

`backend/` is an unrelated earlier utility kept in this repository; it is not
part of the CAM platform.
