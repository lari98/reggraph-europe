# RegGraph Europe

**EU–Swiss Financial Ecosystem Intelligence Platform** — decision support for regulatory
change, cross-border exposure and resilience across the EU-27, Switzerland, the United
Kingdom, the United States and Singapore.

> **Status: Phase 5 of 7 complete (foundation, registry, exposure, insurance, stress lab, PostgreSQL, dashboard, connectors, SSO), release 0.9.0 (rules page for non-experts, rules with real source references).**
> This repository implements the platform core described in the *RegGraph Europe – Pitch Pack*:
> the regulatory-intelligence engine, the bank and corporate registry, insurance connectivity,
> the financial exposure graph, the stress testing laboratory, the security model, multi-language
> and multi-currency support, plus the management dashboard, sandboxed connectors and single sign-on. Later phases add more regulators and the hardening for release 1.0 (see [docs/PHASES.md](docs/PHASES.md)).

The platform is **decision support, not legal advice**. Every rule enters the system as a
draft and becomes effective only after a named compliance officer approves it.

## What works today

| Area | What you get |
|---|---|
| Regulation store | Versioned rules, immutable history, four-eyes approval, logical delete, point-in-time applicability by jurisdiction / entity type / consolidation basis |
| Registry | LEI-keyed banks, insurers and corporates with licences, versioned history, ownership links, indicative control group on any date, and "which rules apply to this entity" ([docs/REGISTRY.md](docs/REGISTRY.md)) |
| Exposure graph | Immutable exposure batches with source, hash and loader; converted edges, concentration (shares, HHI, group view), neighbourhoods; every result has lineage and a reproducibility hash ([docs/EXPOSURE_AND_INSURANCE.md](docs/EXPOSURE_AND_INSURANCE.md)) |
| Insurance connectivity | Versioned insurer-to-insured contracts, reinsurance links, cover concentration, and the *interconnection* view: entities that are both counterparty and insurer |
| Stress lab | Versioned scenarios (currency fall, default with group contagion, haircut, insurer failure), deterministic engine, insurance recovery, capital-buffer breaches; every run freezes its inputs, is signed and can be re-verified ([docs/STRESS_LAB.md](docs/STRESS_LAB.md)) |
| Dashboard | Role-shaped overview (registry, exposure, insurance, stress, rules, rates, connectors) as an API and a small page at `/dashboard`; a role only gets the sections its other endpoints would show ([docs/DASHBOARD_AND_INTEGRATIONS.md](docs/DASHBOARD_AND_INTEGRATIONS.md)) |
| Connectors | ECB reference rates into the rate book; EUR-Lex feed watch that raises *alerts* for people to review (never rules); all outbound calls go through one sandboxed fetcher (allow-list, public addresses only, pinned TLS, size/time caps, no redirects) |
| Single sign-on | OpenID Connect access tokens (RS256/ES256, key set cached, tenant and roles from claims) next to the existing local tokens; secrets are masked in every log record |
| Storage | SQLite by default, PostgreSQL by setting `REGGRAPH_DATABASE_URL`; one migration set for both, whole test suite run on both ([docs/POSTGRESQL.md](docs/POSTGRESQL.md)) |
| Languages | 24 EU official languages + Swiss (de/fr/it/rm), US and UK English, Singapore (English, Chinese, Malay, Tamil) — 34 locale tags, `Accept-Language` negotiation |
| Currencies | EUR plus the six EU members outside the euro (CZK, DKK, HUF, PLN, RON, SEK), CHF, USD, GBP, SGD; legacy BGN / HRK for old data |
| Money | `Decimal` only (no floating point), exact half-even rounding, EUR-triangulated conversion, refuses stale rates |
| Security | Signed tokens with key rotation, role matrix, per-tenant tamper-evident audit chain, rate limiting, strict input hygiene, hardened HTTP headers |
| Quality gates | 714 tests (265 in the attacker-style security suite), 96 % coverage on SQLite (gate 90 %), ruff, mypy `--strict` and bandit clean at the Phase 5 release; the suite passes on Python 3.11 and 3.13 and on PostgreSQL 16 (see the Phase 5 notes in the changelog) |
| Versioning | Every module has its own version and content fingerprint; CI fails if code changed without a bump |

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export REGGRAPH_ENV=dev
export REGGRAPH_TOKEN_KEYS="k1:$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
reggraph serve                                           # http://127.0.0.1:8000/docs  (dev only)

# in another terminal: mint a development token and call the API
TOKEN=$(reggraph issue-token --tenant acme --user ana --role analyst)
curl -H "Authorization: Bearer $TOKEN" -H "Accept-Language: de" http://127.0.0.1:8000/v1/rules
```

See the dashboard with invented data, offline and on `127.0.0.1` only (safe on a train or plane):

```bash
reggraph demo --open          # restarts itself when code changes; the open page updates by itself
reggraph demo --no-watch      # lowest energy use (train, plane)
```

Details, offline guarantees and server deployment: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Run every quality gate: `make check` (and `make audit` for the dependency scan).

## Repository map

```
src/reggraph/
  core/            errors, clock                      (no dependencies)
  jurisdictions/   EU-27 + CH/US/GB/SG registry
  currency/        Money, rates, converter, formatting
  i18n/            locale negotiation, translator, 28 catalogs
  security/        validation, rbac, tokens, rate limit, audit chain
  storage/         SQLite + migrations + DB-level immutability triggers
  regulation/      models, versioning/diff, applicability, store
  registry/        LEI, entities, licences, ownership links, group logic, rule mapping
  fx/              tenant rate book (insert-only)
  api/             FastAPI app, routes, middleware, schemas
  config.py cli.py settings and command line
tests/{unit,integration,security}/
tools/module_manifest.py      per-module versioning
docs/                         architecture, standards, security, i18n, versioning, API
```

## Documentation

| Document | Read it to |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | see layers, data flow and design decisions |
| [docs/REGISTRY.md](docs/REGISTRY.md) | understand entities, ownership, dates and group rules |
| [docs/PHASES.md](docs/PHASES.md) | see the delivery plan mapped to the Pitch Pack modules and gates |
| [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) | follow the tabular coding protocol |
| [docs/SECURITY_AND_TESTING.md](docs/SECURITY_AND_TESTING.md) | map every threat to a control and a test |
| [docs/I18N_AND_CURRENCY.md](docs/I18N_AND_CURRENCY.md) | add a language or currency |
| [docs/VERSIONING.md](docs/VERSIONING.md) | change one module without touching the rest |
| [docs/API.md](docs/API.md) | call the HTTP API |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | run the demo, use offline mode, deploy to a server |
| [docs/ORIGINALITY_AND_REFERENCES.md](docs/ORIGINALITY_AND_REFERENCES.md) | see what is original and what was consulted |

## Known limits (be upfront with customers)

* Translations are **machine drafts** marked for native-speaker review. Legal wording must be
  reviewed before any customer-facing use.
* Rates come from the tenant or from the ECB connector (started by a tenant admin; no built-in
  scheduler yet). Conversion refuses rates older than five days. Live calls to the ECB, EUR-Lex
  and an identity provider were not possible in the build environment: check them in staging.
* SQLite is the default; PostgreSQL is available behind the same storage interface (Phase 4).
* SSO verifies provider tokens; the browser login redirect is done by your front end / provider.
* Group "control" is an indicative test (more than 50 % or a branch), not a full legal consolidation analysis.
* No external penetration test has been performed yet; it is the gate for Phase 7.
