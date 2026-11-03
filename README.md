# RegGraph Europe

**EU–Swiss Financial Ecosystem Intelligence Platform** — decision support for regulatory
change, cross-border exposure and resilience across the EU-27, Switzerland, the United
Kingdom, the United States and Singapore.

> **Status: Phase 1 of 7 complete (foundation).** This repository implements the platform
> core described in the *RegGraph Europe – Pitch Pack*: the regulatory-intelligence engine,
> the security model, multi-language and multi-currency support. Later phases add the
> registry, insurance connectivity, exposure graph, stress lab and dashboard
> (see [docs/PHASES.md](docs/PHASES.md)).

The platform is **decision support, not legal advice**. Every rule enters the system as a
draft and becomes effective only after a named compliance officer approves it.

## What works today

| Area | What you get |
|---|---|
| Regulation store | Versioned rules, immutable history, four-eyes approval, logical delete, point-in-time applicability by jurisdiction / entity type / consolidation basis |
| Languages | 24 EU official languages + Swiss (de/fr/it/rm), US and UK English, Singapore (English, Chinese, Malay, Tamil) — 34 locale tags, `Accept-Language` negotiation |
| Currencies | EUR plus the six EU members outside the euro (CZK, DKK, HUF, PLN, RON, SEK), CHF, USD, GBP, SGD; legacy BGN / HRK for old data |
| Money | `Decimal` only (no floating point), exact half-even rounding, EUR-triangulated conversion, refuses stale rates |
| Security | Signed tokens with key rotation, role matrix, per-tenant tamper-evident audit chain, rate limiting, strict input hygiene, hardened HTTP headers |
| Quality gates | 171 tests (58 attacker-style "hacking" tests), 96 % coverage (gate 90 %), ruff, mypy `--strict`, bandit and pip-audit all clean at the Phase 1 release |
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
| [docs/PHASES.md](docs/PHASES.md) | see the delivery plan mapped to the Pitch Pack modules and gates |
| [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md) | follow the tabular coding protocol |
| [docs/SECURITY_AND_TESTING.md](docs/SECURITY_AND_TESTING.md) | map every threat to a control and a test |
| [docs/I18N_AND_CURRENCY.md](docs/I18N_AND_CURRENCY.md) | add a language or currency |
| [docs/VERSIONING.md](docs/VERSIONING.md) | change one module without touching the rest |
| [docs/API.md](docs/API.md) | call the HTTP API |
| [docs/ORIGINALITY_AND_REFERENCES.md](docs/ORIGINALITY_AND_REFERENCES.md) | see what is original and what was consulted |

## Known limits (be upfront with customers)

* Translations are **machine drafts** marked for native-speaker review. Legal wording must be
  reviewed before any customer-facing use.
* Rates are supplied by the tenant (for example from the ECB reference feed); the platform
  does not fetch them yet. Conversion refuses rates older than five days.
* SQLite is used in Phase 1; PostgreSQL arrives in Phase 4 behind the same storage interface.
* No external penetration test has been performed yet; it is the gate for Phase 7.
