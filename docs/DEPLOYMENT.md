# Running and deploying RegGraph Europe

Status of each route below: **tested** = exercised by the test-suite or measured in a real browser
during development; **not tested** = written from the design, never run against real infrastructure.

## 1. See the dashboard on your own computer (Windows, Chrome) — tested

```bat
cd reggraph-europe
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
reggraph demo --open
```

`reggraph demo` fills an **in-memory** database with invented data (every company is labelled
"(demo)"), starts the server on `127.0.0.1` and opens Chrome on a link such as
`http://127.0.0.1:8000/dashboard#token=...`. The token sits after `#`, which browsers never send to a
server, and the page removes it from the address bar at once. Stop with `Ctrl+C`; the data vanishes.
Options: `--port 8080`, `--role analyst|compliance|viewer` (shows how each role sees a different page),
`--no-watch` (see section 1a).

### 1a. Updates without closing the browser — tested

| What changes | What you do | What happens |
|---|---|---|
| Data (for example an alert is acknowledged) | nothing | the page re-reads the numbers every 30 s while its tab is visible and redraws in place; the header pill says **Live · Updated hh:mm:ss**; click it to pause |
| Page files (`index.html`, `app.js`, `app.css`, `lang.js`) | save the file | the server serves the file fresh from disk (no restart); the open page sees the new build within 30 s and reloads itself, keeping the token, page, language, currency, day and theme |
| Python code or catalogs | save the file | `reggraph demo` notices within 2 s, restarts itself and the open page reconnects; the signing key is stored in `~/.reggraph/demo.key` (owner only), so the token in the open tab stays valid (12 h) |
| A new release (`git pull`, `pip install -e .`) | restart `reggraph demo` once | the open page reconnects and reloads by itself |

If the server is away the pill turns amber (**Reconnecting**) and the page retries with growing pauses
until it is back. A hidden tab makes no request at all. For the lowest energy use (train, plane) start
with `reggraph demo --no-watch`: nothing then runs besides the server, and you can pause the Live pill.

### 1b. Languages

The header has a **Language** menu with all 34 supported locales. English, German, French, Italian and
Spanish are fully translated (navigation, header, Overview, buttons). For every other language the
numbers, dates, currencies and the legal notice follow the chosen language and the remaining interface
text stays English; the menu marks those as "numbers & dates". More languages are added in `lang.js`
(one block per language; a test checks every key and placeholder).

## 2. Train / plane mode — tested

`reggraph demo` always runs offline. For your own data set `REGGRAPH_OFFLINE=1` before `reggraph serve`.

| Guarantee in offline mode | How it is enforced | Test |
|---|---|---|
| No outbound connection | connectors use `OfflineFetcher`; `POST /v1/connectors/.../run` is refused | `test_connectors_are_switched_off_and_never_touch_the_network` (sockets patched to fail) |
| Nothing reachable from the Wi-Fi/network | `serve` and `demo` refuse any host except 127.0.0.1, ::1, localhost | `test_serve_refuses_a_public_address_in_offline_mode` |
| No SSO/identity-provider call | configuration with SSO or without local tokens is rejected at start-up | `test_offline_mode_rejects_sso_configuration` |
| Page makes no external request | browser test records every request | `test_every_page_renders_cleanly_and_stays_local` |
| Live refresh costs nothing while hidden or paused | no request at all while paused; timer skips hidden tabs | `test_live_pill_pauses_and_reconnects` |

Energy: one process, one worker, no access log. With `--no-watch` there is no file watching; the open
page makes one tiny request every 30 s while visible (pausable, none while hidden). Measured on the
development container before live updates were added: **0.03 CPU-seconds in 20 s idle (~0.15 %), 70 MB
memory**; the watcher adds one directory scan every 2 s. Your laptop will differ; close the tab and the
server uses nothing but memory.

## 3. Put it on a server so stakeholders can open it from anywhere — not tested

You need: a Linux server or container host, a domain name, a TLS certificate. Never expose the plain
HTTP port to the internet.

1. **Database**: PostgreSQL 16 (`pip install -e ".[postgres]"`), `REGGRAPH_DATABASE_URL=postgresql://user:pass@host/db`.
   The PostgreSQL back end is covered by the test-suite (CI service container needed; see README).
2. **Secrets**: `REGGRAPH_ENV=prod`, `REGGRAPH_TOKEN_KEYS="k1:<48+ random characters>"` from a secret
   store, never from the repository. Rotate by adding `k2`, switching `REGGRAPH_ACTIVE_KID`, then removing `k1`.
3. **Sign-in**: for real users configure SSO (`REGGRAPH_OIDC_ISSUER`, `_AUDIENCE`, `_JWKS_URL`, claims);
   when it works set `REGGRAPH_ALLOW_LOCAL_TOKENS=0`. See `docs/DASHBOARD_AND_INTEGRATIONS.md`.
4. **Run**: `reggraph serve --host 127.0.0.1 --port 8000` behind a reverse proxy (nginx, Caddy, a cloud
   load balancer) that terminates TLS 1.2+ and forwards to port 8000. Add HSTS at the proxy.
5. **Data for viewers**: load real data through the API (`/v1/entities`, `/v1/exposures/batches`, ...)
   with an account of the right role; the dashboard shows each person only what their role permits.
   Do **not** run `reggraph demo` on a server: it refuses non-loopback addresses on purpose.
6. **Connectors** (ECB rates, EUR-Lex watch) work only online and only for the allow-listed hosts; they
   have never been run against the live sites by the developers (no internet in the build environment).
   Run each once and check the result before relying on it.
7. **Backups and monitoring**: not yet delivered (Phase 7).

A container image is *not* part of 0.5.1; do not rely on any Dockerfile until Phase 7 provides a tested one.

## 3a. What the console contains (all read-only except where noted)

| Page | Drill-down | Source endpoints |
|---|---|---|
| Overview | KPI cards, donuts, bars and the network link into every other page | `/v1/dashboard`, `/v1/exposures/edges` |
| Entities | filter, sort, CSV; row opens the entity | `/v1/entities` |
| Entity (7 tabs) | profile, ownership/group, concentration (HHI), insurance cover, applicable rules, ecosystem graph, version history | `/v1/entities/{lei}/...` |
| Exposure | relationships, concentration per holder, data-batch lineage | `/v1/exposures/...` |
| Insurance | contracts with history drawer | `/v1/contracts` |
| Stress tests | run detail, loss drivers, frozen inputs, **Verify** (recomputes and checks the signature) | `/v1/stress/...` |
| Rules | library with decision trail drawer, review tasks | `/v1/rules`, `/v1/review-tasks` |
| Data & audit | audit-chain check, audit trail, connectors (disabled offline), alerts (acknowledge) | `/v1/audit`, `/v1/connectors/...`, `/v1/source-alerts` |

Every number comes from the API with the caller's role applied; the page adds no data of its own.
Interface wording outside the section titles is English in this release (all 28 catalogs cover titles only).

## 4. Quick checks before showing it to a stakeholder

- `python tools/module_manifest.py check` and `pytest` are green.
- `pytest tests/ui` passes (needs `pip install playwright && playwright install chromium`).
- Open the demo in Chrome at 100 % zoom, then narrow the window to phone width: no sideways scrolling.
- Switch Windows to dark mode: the page follows it automatically.
