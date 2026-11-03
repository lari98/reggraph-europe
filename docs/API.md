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
