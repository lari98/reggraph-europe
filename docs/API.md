# HTTP API (v1)

Base path `/v1`. JSON in, JSON out. Interactive docs at `/docs` exist only when
`REGGRAPH_ENV` is not `prod`.

**Authentication:** `Authorization: Bearer <token>` (tokens from your identity provider; in
development `reggraph issue-token`). **Language:** send `Accept-Language`; the reply carries
`Content-Language`. Every response has `X-Request-ID` — quote it when reporting a problem.

## Endpoints

| Verb | Path | Permission | Purpose |
|---|---|---|---|
| GET | `/health` | public (not rate limited) | liveness + version |
| GET | `/v1/locales` | public | the 34 supported locale tags |
| GET | `/v1/jurisdictions` | public | EU-27 + CH/US/GB/SG with currency and `euro_area` |
| GET | `/v1/currencies` | public | supported currencies |
| POST | `/v1/convert` | `fx:convert` | convert an amount |
| PUT | `/v1/fx-rates` | `fx:write` | record a day's rates (insert-only) |
| POST | `/v1/rules` | `rules:propose` | propose a rule version (creates a DRAFT) |
| POST | `/v1/ingest` | `rules:ingest` | machine-found candidate (DRAFT + review task) |
| GET | `/v1/rules` | `rules:read` | current approved rules |
| GET | `/v1/rules/applicable` | `rules:read` | rules for `jurisdiction`, `entity_type`, `basis`, optional `on` |
| GET | `/v1/rules/{key}` | `rules:read` | full version history |
| POST | `/v1/rules/{key}/versions/{n}/approve` | `rules:approve` | approve (not your own proposal) |
| POST | `/v1/rules/{key}/versions/{n}/reject` | `rules:approve` | reject a draft |
| POST | `/v1/rules/{key}/withdraw` | `rules:withdraw` | logical delete; history kept |
| GET | `/v1/review-tasks` | `tasks:read` | open review tasks |
| GET | `/v1/audit` | `audit:read` | audit entries (`limit` ≤ 500, `after`) |
| GET | `/v1/audit/verify` | `audit:read` | hash-chain check + current head |
| PUT | `/v1/entities/{lei}` | `registry:write` | create or update an entity (new version) |
| GET | `/v1/entities` | `registry:read` | current entities; filters `jurisdiction`, `entity_type`, `status`; cursor `after`, `limit` ≤ 200 |
| GET | `/v1/entities/{lei}` | `registry:read` | current version |
| GET | `/v1/entities/{lei}/history` | `registry:read` | all versions |
| POST | `/v1/ownership` | `registry:write` | add an ownership link |
| POST | `/v1/ownership/{id}/end` | `registry:write` | give an open link its last day |
| GET | `/v1/entities/{lei}/ownership` | `registry:read` | owners and holdings on `on` |
| GET | `/v1/entities/{lei}/group` | `registry:read` | controlled group on `on` (indicative) |
| GET | `/v1/entities/{lei}/ultimate-parent` | `registry:read` | top of the group on `on` |
| GET | `/v1/entities/{lei}/rules` | `registry:read` + `rules:read` | rules that apply to the entity on `on` |
| POST | `/v1/exposures/batches` | `exposure:write` | load a batch (≤ 1000 positions); 409 if the same content was loaded, 422 if a LEI is not registered |
| GET | `/v1/exposures/batches`, `/v1/exposures/batches/{id}` | `exposure:read` | batches (source, hash, loader) — the lineage anchor |
| GET | `/v1/exposures/edges` | `exposure:read` | who is exposed to whom; `on`, `currency` (default EUR), `max_batch` |
| GET | `/v1/exposures/concentration/{lei}` | `exposure:read` | shares, HHI, top-1/top-3; `by_group` |
| GET | `/v1/exposures/neighbourhood/{lei}` | `exposure:read` | exposure graph within `depth` 0–4 |
| PUT | `/v1/contracts/{ref}` | `insurance:write` | new contract version (path ref must equal body ref) |
| GET | `/v1/contracts`, `/v1/contracts/{ref}`, `/v1/contracts/{ref}/history` | `insurance:read` | filters `insurer`, `insured`, `line`; cursor `after` |
| GET | `/v1/entities/{lei}/insurance` | `insurance:read` | cover of an entity on `on`; `by_group` |
| GET | `/v1/entities/{lei}/interconnection` | `exposure:read` + `insurance:read` | entities that are both counterparty and insurer |
| GET | `/v1/entities/{lei}/ecosystem` | `exposure:read` + `insurance:read` + `registry:read` | exposure, insurance and ownership edges within `depth` |
| PUT | `/v1/stress/scenarios/{key}` | `stress:write` | new scenario version (path key must equal body key) |
| GET | `/v1/stress/scenarios`, `/v1/stress/scenarios/{key}` (`?version=`), `…/history` | `stress:read` | scenarios |
| POST | `/v1/stress/runs` | `stress:write` + exposure, insurance, registry read | run, sign, store; body: `scenario_key`, optional `scenario_version`, `as_of`, `currency`, `max_batch`, `capital_buffers` |
| GET | `/v1/stress/runs`, `/v1/stress/runs/{id}` | `stress:read` | run records; one run with result |
| GET | `/v1/stress/runs/{id}/snapshot` | `stress:read` | the frozen inputs |
| POST | `/v1/stress/runs/{id}/verify` | `stress:read` | recompute and check hashes and signature |
| GET | `/v1/dashboard` | `dashboard:read` | role-shaped overview; `currency`, `as_of`; sections need their own read permission |
| GET | `/dashboard` (+ `/dashboard/app.js`, `/dashboard/app.css`) | public (no data) | the dashboard page |
| POST | `/v1/connectors/{name}/run` | `connectors:run` | run `ecb_rates` or `eurlex_watch` (409 if run in the last 5 minutes) |
| GET | `/v1/connectors/runs` | `connectors:read` | run history; `connector`, `limit` ≤ 200, `after` |
| GET | `/v1/source-alerts` | `connectors:read` | EUR-Lex watch alerts; `status` open/acknowledged |
| POST | `/v1/source-alerts/{id}/acknowledge` | `alerts:ack` | mark an alert as reviewed |

Stress lab details: [STRESS_LAB.md](STRESS_LAB.md).

Phase 3 details, rules and lineage: [EXPOSURE_AND_INSURANCE.md](EXPOSURE_AND_INSURANCE.md).
Amounts are decimal strings; `on` defaults to today, `currency` to EUR, and ids in paths are
range-checked (a number beyond the database range is 422, not 500).

Registry details, date rules and examples: [REGISTRY.md](REGISTRY.md). `{lei}` is checked for
format and check digits (422 if wrong); the LEI in the path and the body of a `PUT` must match.

Roles → permissions are tabulated in `src/reggraph/security/rbac.py`.

## Examples

Convert from euro to a non-euro EU currency:

```http
POST /v1/convert
{"amount": "1000", "from": "EUR", "to": "CZK"}

200 {"source": {"amount": "1000.00", "currency": "EUR"},
     "result": {"amount": "25000.00", "currency": "CZK"},
     "rate": "25.0000000000", "rate_date": "2026-03-02",
     "sources": ["ECB reference"], "display": "…"}
```

Propose a rule (fields as in `RuleInput`; unknown fields are rejected):

```http
POST /v1/rules
{"change_summary": "Initial entry",
 "rule": {"rule_key": "EU.CRR3.CET1", "title": "Minimum CET1 ratio",
          "citation": "Regulation (EU) 2024/1623, Art. 92",
          "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1623/oj",
          "jurisdictions": ["EU"], "entity_types": ["bank"], "consolidation": "consolidated",
          "publication_date": "2024-06-19", "effective_date": "2024-07-09",
          "application_date": "2025-01-01", "control_owner": "Head of Regulatory Reporting",
          "numeric_requirements": [{"name": "CET1", "comparator": ">=", "value": "4.5", "unit": "percent"}]}}
```

## Errors

Always `{"code", "message", "request_id"}`; `message` is in the caller's language.

| Status | `code` | Meaning |
|---|---|---|
| 401 | `unauthenticated` | missing, invalid or expired token (`WWW-Authenticate: Bearer`) |
| 403 | `forbidden` | role lacks permission, or you tried to approve your own proposal |
| 404 | `not_found` | unknown rule/version — or it belongs to another tenant |
| 409 | `conflict` | open draft exists / version already decided / identical entity / ownership cycle, duplicate or over 100 % |
| 413 | `payload_too_large` | body over the configured limit (default 256 KiB) |
| 422 | `invalid_input` | validation failed; `problems` lists `loc` and `type` only |
| 429 | `rate_limited` | slow down; see `Retry-After` |
| 500 | `internal_error` | generic; details only in the server log under `request_id` |
