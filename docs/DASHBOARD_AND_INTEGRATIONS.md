# Dashboard, connectors and single sign-on (Phase 5)

Modules: `netio` (sandboxed outbound access), `connectors` (ECB rates, EUR-Lex watch),
`dashboard` (role-shaped overview and page), `security.oidc` (SSO), `core.logsafe` (log secrecy).

## 1. Management dashboard

* **Page:** `GET /dashboard` (plus `/dashboard/app.js`, `/dashboard/app.css`). The page itself
  contains no data. The user pastes an access token; the script calls `GET /v1/dashboard`.
  The token lives only in the page's memory and password field (never in storage, cookies or URLs).
* **Data:** `GET /v1/dashboard?currency=EUR&as_of=YYYY-MM-DD` returns one object:

| Section | Needs permission | Shows |
|---|---|---|
| `registry` | `registry:read` | entities by type and jurisdiction, active count, ownership links on the date |
| `exposure` | `exposure:read` | positions, batches, total and top-5 counterparties in the chosen currency |
| `insurance` | `insurance:read` | current contracts by line |
| `stress` | `stress:read` | scenario count, run count, last 5 runs with gross/net loss and breaches |
| `rules` | `rules:read` | rule versions by status (+ open review tasks with `tasks:read`) |
| `rates` | `fx:convert` | latest stored rate date and number of currencies |
| `connectors` | `connectors:read` | last run of each connector, open source alerts |

Sections the caller may not see are listed under `omitted` (the page greys them out). A section
that cannot be built (for example a missing exchange rate) returns `{"error": "<code>"}` for that
section only. `view` names the caller's main role view: `administration`, `compliance`,
`analysis` or `overview`. `labels` carries the section titles in the caller's language
(`Accept-Language`, all 28 catalogs).
* **Not included in Phase 5:** charts, saved views, export, and translated row labels inside a
  section (row names are the API's field names).

## 2. Connectors

Both connectors are started by a tenant admin (`POST /v1/connectors/{name}/run`, `name` is
`ecb_rates` or `eurlex_watch`). Nothing runs on its own schedule yet; call it from your
scheduler (cron, Kubernetes CronJob) with a service identity that holds `tenant_admin`.

| Connector | Source | Effect |
|---|---|---|
| `ecb_rates` | ECB daily reference-rate XML | Stores the day's rates through the normal rate book (so insert-only, no future dates, 3-letter supported currencies only; others are counted as `skipped`). Day already stored → status `unchanged`. File older than 14 days → `failed` (stale). |
| `eurlex_watch` | an EUR-Lex RSS/Atom feed URL you configure | Creates one **alert** per new item (keyed by CELEX number or link hash). Never creates or changes a rule: a person reviews the alert (`POST /v1/source-alerts/{id}/acknowledge`, compliance officer or tenant admin) and proposes a rule through the normal four-eyes process. |

Each run is stored immutably (`GET /v1/connectors/runs`) with status `succeeded`, `unchanged` or
`failed`, plus an audit entry. A failed run stores only a short error code. At most one run per
connector and tenant every five minutes (HTTP 409 otherwise).

### Sandbox (what the connector review checks)

| # | Control |
|---|---|
| N1–N3 | HTTPS only, port 443, no credentials in URLs, exact host allow-list (`REGGRAPH_CONNECTOR_HOSTS`), no spaces/control characters/backslashes/non-ASCII in URLs |
| N4 | Host resolved once; **every** address must be public (loopback, private, link-local, CGNAT, multicast, metadata, IPv4-mapped IPv6 are refused) |
| N5 | Connection pinned to the checked address (no DNS rebinding); TLS certificate verified for the real host name; TLS ≥ 1.2 |
| N6 | Redirects are never followed |
| N7 | 2 MiB body cap, 10 s socket timeout, 20 s overall deadline |
| N8 | Only expected content types; no compressed bodies requested |
| C2 | URLs come from start-up settings, never from a request; callers cannot choose host, path or port |
| E1/R1 | XML parsed with `defusedxml` (no DTD, entities, external references) |
| R3 | Feed links kept only if `https://eur-lex.europa.eu/...`; text cleaned (no control/bidi/zero-width characters) |

The only code that opens sockets is `reggraph.netio`.

## 3. Single sign-on (OpenID Connect)

RegGraph acts as a **resource server**: your identity provider (with MFA) signs a JWT access
token and RegGraph verifies it. The browser redirect login itself is done by your front end or
the provider and is **not** part of this release.

Configure all three, or none: `REGGRAPH_OIDC_ISSUER`, `REGGRAPH_OIDC_AUDIENCE`,
`REGGRAPH_OIDC_JWKS_URL`. The provider must add two claims (names configurable):
`reggraph_tenant` (string tenant id) and `reggraph_roles` (list of role names:
`viewer`, `analyst`, `compliance_officer`, `tenant_admin`, `ingestion_service`).

Checks: RS256/ES256 only (HMAC and `none` refused before any key lookup); key chosen by `kid`
from the provider's key set only (embedded `jwk`/`jku`/`x5u` ignored); exact issuer and audience;
`exp`, `iat`, `sub` required; 60 s skew, 12 h maximum lifetime; unknown or malformed roles reject the
token. Key set cached 10 minutes; an unknown `kid` triggers at most one refresh per minute; if the
provider is down the cached keys keep working (so a revoked key stays trusted until the next
successful refresh — keep the provider's key lifetime short).

Token routing: tokens starting `v1.` are RegGraph's own HMAC tokens; anything else goes to the
SSO verifier. Once SSO is live set `REGGRAPH_ALLOW_LOCAL_TOKENS=false` so only the provider can
log people in (the service refuses that setting without SSO).

## 4. No secrets in logs

`reggraph.core.logsafe` rewrites every log record at creation: bearer tokens, RegGraph tokens,
JWTs, `password=/secret=/token=/api_key=/dsn=` values and passwords inside URLs are masked,
including inside exception tracebacks. `Settings` hides keys and DSNs from `repr`. Error bodies
never echo input. Masking is a safety net: secrets are still never passed to log calls.

## 5. Honest limits

* The sandbox this was built in has no route to the ECB, EUR-Lex or an identity provider.
  Parsers, the fetcher (against a local TLS server), the SSO verifier (against generated keys)
  and the connector logic are tested; **a live call to each real service has not been made**.
  Do a first run in a staging environment and check the ECB URL and your EUR-Lex feed URL.
* EUR-Lex offers several feed and API formats; the feed parser accepts standard RSS 2.0 and
  Atom. Confirm that the feed you choose carries the link (and ideally the CELEX number) per item.
* ECB rates are decision-support data. The ECB's terms of use apply; check them before commercial
  redistribution.
* Not yet: scheduling, SNB/MAS rate feeds, token revocation lists, an in-app login flow.

## References (concepts only; code is original)

ECB euro foreign exchange reference rates (published XML layout); OpenID Connect Core 1.0 and
RFC 8725 (JWT best current practice); OWASP SSRF Prevention Cheat Sheet; OWASP XML External
Entity Prevention Cheat Sheet.
