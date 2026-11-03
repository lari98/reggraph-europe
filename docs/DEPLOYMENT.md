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
Options: `--port 8080`, `--role analyst|compliance|viewer` (shows how each role sees a different page).

## 2. Train / plane mode — tested

`reggraph demo` always runs offline. For your own data set `REGGRAPH_OFFLINE=1` before `reggraph serve`.

| Guarantee in offline mode | How it is enforced | Test |
|---|---|---|
| No outbound connection | connectors use `OfflineFetcher`; `POST /v1/connectors/.../run` is refused | `test_connectors_are_switched_off_and_never_touch_the_network` (sockets patched to fail) |
| Nothing reachable from the Wi-Fi/network | `serve` and `demo` refuse any host except 127.0.0.1, ::1, localhost | `test_serve_refuses_a_public_address_in_offline_mode` |
| No SSO/identity-provider call | configuration with SSO or without local tokens is rejected at start-up | `test_offline_mode_rejects_sso_configuration` |
| Page makes no external request and no timers | browser test records every request; page has no polling | `test_page_renders_cleanly` |

Energy: one process, one worker, no reload, no access log, no background polling. Measured on the
development container: **0.03 CPU-seconds in 20 s idle (~0.15 %), 70 MB memory**. Your laptop will differ;
close the tab and the server uses nothing but memory.

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

## 4. Quick checks before showing it to a stakeholder

- `python tools/module_manifest.py check` and `pytest` are green.
- `pytest tests/ui` passes (needs `pip install playwright && playwright install chromium`).
- Open the demo in Chrome at 100 % zoom, then narrow the window to phone width: no sideways scrolling.
- Switch Windows to dark mode: the page follows it automatically.
