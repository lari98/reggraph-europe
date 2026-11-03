# Delivery phases

Maps the Pitch Pack's **six modules** and **five MVP stages** to build phases, and adds a final
hardening phase. A phase is *done* only when its exit gate passes.

| Phase | Name | Pitch Pack module / MVP stage | Main deliverables | Exit gate |
|---|---|---|---|---|
| **1 ✅** | Foundation | Regulatory Intelligence (core) · Stage 1 | Jurisdictions, currency, 34 locales, rule store, security core, API, tests, tooling | `make check` green; ≥ 90 % coverage; attacker tests pass |
| **2 ✅** | Registry | Bank & Corporate Registry · Stage 1 | LEI-keyed entities, licences, ownership and group structures, consolidation basis, entity ↔ rule mapping ([REGISTRY.md](REGISTRY.md)) | Entity data validated (LEI checksum, cycles, 100 % rule); tenant isolation attacks pass |
| **3 ✅** | Connectivity & graph | Insurance Connectivity, Financial Exposure Graph · Stage 2 | Insurer ↔ bank/corporate contracts, immutable exposure batches with lineage, concentration (HHI), neighbourhood and combined ecosystem graph ([EXPOSURE_AND_INSURANCE.md](EXPOSURE_AND_INSURANCE.md)) | Graph queries reproducible; data lineage recorded |
| **4 ✅** | Stress lab | Stress Testing Laboratory · Stage 3 | Scenario engine (fx, default with group contagion, haircut, insurer failure), insurance recovery, frozen snapshots, signed and verifiable runs ([STRESS_LAB.md](STRESS_LAB.md)); PostgreSQL storage ([POSTGRESQL.md](POSTGRESQL.md)) | Same inputs → same outputs; results signed and versioned |
| **5 ✅** | Dashboard & integrations | Management Dashboard · Stage 4 | Role-shaped dashboard and page, sandboxed connectors (ECB rates, EUR-Lex watch), OIDC single sign-on, secret-safe logging ([DASHBOARD_AND_INTEGRATIONS.md](DASHBOARD_AND_INTEGRATIONS.md)) | Connector sandbox review; no secrets in logs |
| 6 | Expansion | Stage 5 | FINMA, FCA, SEC, MAS rule packs; more locales and currencies | Native-speaker translation review signed off |
| 7 | Hardening & release | Whole system | External penetration test, load test, backup / restore drill, threat-model review, release 1.0 | No open high/critical findings |

## Rules for every phase

1. New code arrives with unit, integration and (where it touches input or access) attacker tests.
2. `make check` and `make audit` pass before merge.
3. Every touched module is bumped with `tools/module_manifest.py bump` — see [VERSIONING.md](VERSIONING.md).
4. New text uses translation keys, not string literals; new money uses `Money`, never `float`.
5. Architecture decisions are added to the table in [ARCHITECTURE.md](ARCHITECTURE.md).

## What the delivered phases deliberately leave out

Scheduled connector runs, the browser redirect login for SSO, charts/export in the dashboard, SNB/MAS rate feeds,
FINMA/FCA/SEC/MAS rule packs and data migration from SQLite to PostgreSQL. They are scheduled above or listed in
[DASHBOARD_AND_INTEGRATIONS.md](DASHBOARD_AND_INTEGRATIONS.md); nothing delivered so far pretends to provide them.
Live calls to the ECB, EUR-Lex and an identity provider were not possible in the build environment and must be
checked in staging.
