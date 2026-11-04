"""``reggraph demo``: a self-contained, offline showcase with invented data.

* Runs in **offline mode** (no outbound connection can be made) and only on the loopback
  address, so it is safe on a train or a plane and exposes nothing to the network.
* Data lives in memory only and disappears when the process stops.
* Every entity name ends with "(demo)"; rates, positions and alerts are made up. Nothing here
  is market data or legal content.
* The printed link carries a short-lived token in the URL *fragment* (the part after ``#``),
  which browsers never send to a server; the page removes it from the address bar at once.
* The signing key is kept in ``~/.reggraph/demo.key`` (owner-only) so that restarting the demo
  does not sign an open page out. It signs tokens for invented data on the loopback address only.
* By default the demo restarts itself when a Python or catalog file changes (see
  :func:`run_demo_watched`); page files are served fresh from disk, so they need no restart.
"""

from __future__ import annotations

import secrets
import subprocess  # nosec B404
import sys
import time
import webbrowser
from datetime import timedelta
from pathlib import Path
from typing import Any

from reggraph.api.deps import Services, build_services
from reggraph.config import Settings
from reggraph.core.clock import SystemClock
from reggraph.exposure import BatchInput
from reggraph.insurance import ContractInput
from reggraph.registry import EntityInput, OwnershipInput
from reggraph.regulation.models import RuleInput
from reggraph.security.rbac import Principal, Role
from reggraph.stress import ScenarioInput

TENANT = "demo"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
TOKEN_HOURS = 12
WATCH_SECONDS = 2.0
_WATCHED_SUFFIXES = {".py", ".json"}  # page files (html/js/css) are served fresh, no restart
_PACKAGE_DIR = Path(__file__).resolve().parent


def make_lei(seed: str) -> str:
    """A checksum-valid LEI from an 18-character body (demo identifiers only)."""
    body = (seed.upper() + "0" * 18)[:18]
    digits = "".join(str(int(ch, 36)) for ch in body + "00")
    return body + f"{98 - int(digits) % 97:02d}"


# key -> (name, type, country, currency)
_ENTITIES: dict[str, tuple[str, str, str, str]] = {
    "ALPENBANK": ("Alpenbank AG (demo)", "bank", "CH", "CHF"),
    "NORDHAFEN": ("Nordhafen Bank SE (demo)", "bank", "DE", "EUR"),
    "RHONECRED": ("Rhône Crédit SA (demo)", "bank", "FR", "EUR"),
    "LOMBARDIA": ("Lombardia Banca S.p.A. (demo)", "bank", "IT", "EUR"),
    "VISTULA": ("Vistula Bank S.A. (demo)", "bank", "PL", "PLN"),
    "ASSEKURANZ": ("Matterhorn Assekuranz AG (demo)", "insurer", "CH", "CHF"),
    "RHEINVERS": ("Rheinland Versicherung AG (demo)", "insurer", "DE", "EUR"),
    "ATLANTICRE": ("Atlantic Rück SE (demo)", "reinsurer", "DE", "EUR"),
    "KRONOSIND": ("Kronos Industrie AG (demo)", "corporate", "DE", "EUR"),
    "SOLARIS": ("Solaris Energie SA (demo)", "corporate", "ES", "EUR"),
    "TULIP": ("Tulp Logistiek B.V. (demo)", "corporate", "NL", "EUR"),
    "MERIDIAN": ("Meridian Foods Ltd (demo)", "corporate", "GB", "GBP"),
    "LIONCITY": ("Lion City Trading Pte (demo)", "corporate", "SG", "SGD"),
    "NEWPORT": ("Newport Capital Inc (demo)", "corporate", "US", "USD"),
    "KRONOSHLD": ("Kronos Holding AG (demo)", "holding", "DE", "EUR"),
}
L = {k: make_lei(k) for k in _ENTITIES}

# (holder, counterparty, type, currency, amount)
_POSITIONS = (
    ("ALPENBANK", "KRONOSIND", "loan", "EUR", "420000000"),
    ("ALPENBANK", "NEWPORT", "bond", "USD", "310000000"),
    ("ALPENBANK", "SOLARIS", "loan", "EUR", "180000000"),
    ("NORDHAFEN", "KRONOSIND", "loan", "EUR", "650000000"),
    ("NORDHAFEN", "MERIDIAN", "bond", "GBP", "240000000"),
    ("NORDHAFEN", "RHONECRED", "deposit", "EUR", "380000000"),
    ("NORDHAFEN", "LIONCITY", "derivative", "SGD", "95000000"),
    ("RHONECRED", "SOLARIS", "loan", "EUR", "520000000"),
    ("RHONECRED", "TULIP", "loan", "EUR", "210000000"),
    ("RHONECRED", "LOMBARDIA", "repo", "EUR", "300000000"),
    ("LOMBARDIA", "TULIP", "loan", "EUR", "150000000"),
    ("LOMBARDIA", "VISTULA", "bond", "PLN", "820000000"),
    ("VISTULA", "KRONOSIND", "guarantee", "EUR", "110000000"),
    ("VISTULA", "NEWPORT", "bond", "USD", "125000000"),
    ("ALPENBANK", "NORDHAFEN", "deposit", "CHF", "260000000"),
)
_RATES = {"USD": "1.1600", "CHF": "0.9300", "PLN": "4.2500", "GBP": "0.8700", "SGD": "1.5000"}


def seed(services: Services, *, rich: bool = False) -> dict[str, Principal]:
    """Fill an empty database with invented data. Returns one principal per demo role.

    ``rich=True`` adds the large realistic set from :mod:`reggraph.demo_data`.
    Tests use the small set."""
    today = services.clock.now().date()
    people = {
        "admin": Principal("demo.admin", TENANT, frozenset({Role.TENANT_ADMIN})),
        "analyst": Principal("demo.analyst", TENANT, frozenset({Role.ANALYST})),
        "compliance": Principal("demo.compliance", TENANT, frozenset({Role.COMPLIANCE_OFFICER})),
        "viewer": Principal("demo.viewer", TENANT, frozenset({Role.VIEWER})),
    }
    analyst, admin = people["analyst"], people["admin"]
    for key, (name, kind, country, cur) in _ENTITIES.items():
        entity = EntityInput.model_validate(
            {
                "lei": L[key],
                "legal_name": name,
                "entity_type": kind,
                "jurisdiction": country,
                "reporting_currency": cur,
                "reporting_basis": "consolidated" if kind in {"bank", "holding"} else "individual",
            }
        )
        services.registry.upsert(analyst, entity, "demo data")
    for parent, child, pct in (
        ("KRONOSHLD", "KRONOSIND", "100"),
        ("RHEINVERS", "ATLANTICRE", "35"),
        ("NORDHAFEN", "VISTULA", "62.5"),
    ):
        services.registry.link(
            analyst,
            OwnershipInput.model_validate(
                {
                    "parent_lei": L[parent],
                    "child_lei": L[child],
                    "percent": pct,
                    "effective_from": "2020-01-01",
                }
            ),
        )
    services.ratebook.put(admin, today, "demo rates (invented, not market data)", _RATES)
    contracts = (
        ("D-NL-001", "ASSEKURANZ", "ALPENBANK", "non_life", "CHF", "900000000"),
        ("D-NL-002", "RHEINVERS", "NORDHAFEN", "non_life", "EUR", "600000000"),
        ("D-CR-003", "RHEINVERS", "RHONECRED", "credit_and_surety", "EUR", "400000000"),
        ("D-LF-004", "ASSEKURANZ", "KRONOSIND", "life", "EUR", "120000000"),
    )
    for ref, insurer, insured, line, cur, amount in contracts:
        services.contracts.upsert(
            analyst,
            ContractInput.model_validate(
                {
                    "contract_ref": ref,
                    "insurer_lei": L[insurer],
                    "insured_lei": L[insured],
                    "line": line,
                    "currency": cur,
                    "sum_insured": amount,
                    "start_date": "2025-01-01",
                }
            ),
            "demo data",
        )
    services.contracts.upsert(
        analyst,
        ContractInput.model_validate(
            {
                "contract_ref": "D-RE-005",
                "insurer_lei": L["ATLANTICRE"],
                "insured_lei": L["RHEINVERS"],
                "line": "reinsurance",
                "currency": "EUR",
                "sum_insured": "500000000",
                "start_date": "2025-01-01",
                "reinsures_ref": "D-NL-002",
            }
        ),
        "demo data",
    )
    positions: list[dict[str, Any]] = [
        {
            "exposure_ref": f"DEMO-{i:03d}",
            "holder_lei": L[h],
            "counterparty_lei": L[c],
            "exposure_type": t,
            "currency": cur,
            "amount": amt,
            "as_of": (today - timedelta(days=1)).isoformat(),
        }
        for i, (h, c, t, cur, amt) in enumerate(_POSITIONS, 1)
    ]
    services.exposures.load_batch(
        analyst,
        BatchInput.model_validate(
            {"source_system": "demo-generator", "description": "invented", "positions": positions}
        ),
    )
    scenarios = {
        "DEMO-DEFAULT": [
            {"kind": "default", "leis": [L["KRONOSIND"]], "loss_given_default_percent": "45"}
        ],
        "DEMO-FX": [{"kind": "fx", "currency": "USD", "change_percent": "-20"}],
        "DEMO-INSURER": [
            {"kind": "default", "leis": [L["SOLARIS"]], "loss_given_default_percent": "60"},
            {"kind": "insurer_failure", "leis": [L["RHEINVERS"]]},
        ],
    }
    for key, shocks in scenarios.items():
        services.stress_store.upsert_scenario(
            analyst,
            ScenarioInput.model_validate(
                {
                    "scenario_key": key,
                    "title": key.replace("-", " ").title(),
                    "description": "demo scenario",
                    "shocks": shocks,
                    "use_insurance": True,
                }
            ),
            "demo data",
        )
        services.stress.run(
            analyst,
            key,
            on=today,
            currency="EUR",
            buffers={
                L["ALPENBANK"]: "30000000",
                L["NORDHAFEN"]: "500000000",
                L["RHONECRED"]: "100000000",
                L["VISTULA"]: "30000000",
            },
        )
    rule = RuleInput.model_validate(
        {
            "rule_key": "DEMO.CET1.MIN",
            "title": "Demo: minimum CET1 ratio (invented text)",
            "citation": "Demo citation - not a real legal reference",
            "jurisdictions": ["EU"],
            "entity_types": ["bank"],
            "consolidation": "consolidated",
            "publication_date": "2024-06-19",
            "effective_date": "2024-07-09",
            "application_date": "2025-01-01",
            "control_owner": "Demo Compliance",
            "numeric_requirements": [
                {"name": "CET1", "comparator": ">=", "value": "4.5", "unit": "percent"}
            ],
        }
    )
    services.rules.propose(analyst, rule, "demo rule")
    services.rules.approve(people["compliance"], "DEMO.CET1.MIN", 1, "demo approval")
    services.rules.propose(
        analyst,
        RuleInput.model_validate({**rule.model_dump(mode="json"), "rule_key": "DEMO.LCR.MIN"}),
        "demo draft awaiting review",
    )
    now = services.clock.now().isoformat()
    for n, title in enumerate(
        (
            "Demo alert: new delegated act on reporting (sample)",
            "Demo alert: amended directive (sample)",
        ),
        1,
    ):
        services.db.execute(
            "INSERT INTO source_alerts (tenant_id, alert_id, source, item_key, title, url,"
            " published, status, created_at) VALUES (?, ?, 'eurlex', ?, ?, ?, NULL, 'open', ?)",
            (TENANT, n, f"demo:{n}", title, "https://eur-lex.europa.eu/", now),
        )
    if rich:
        from reggraph.demo_data import extend

        extend(services, people, dict(L), today)
    return people


def demo_key(home: Path | None = None) -> bytes:
    """A signing key that survives restarts (stored owner-only); random if the disk is read-only."""
    folder = (home or Path.home()) / ".reggraph"
    file = folder / "demo.key"
    try:
        if file.is_file():
            key = file.read_bytes().strip()
            if len(key) >= 32:
                return key
        folder.mkdir(mode=0o700, exist_ok=True)
        key = secrets.token_urlsafe(48).encode()
        file.write_bytes(key)
        file.chmod(0o600)
        return key
    except OSError:
        return secrets.token_urlsafe(48).encode()


def _snapshot() -> dict[str, int]:
    """Modification time of every file whose change needs a restart (cheap: a few hundred stats)."""
    return {
        str(p): p.stat().st_mtime_ns
        for p in _PACKAGE_DIR.rglob("*")
        if p.suffix in _WATCHED_SUFFIXES and "__pycache__" not in p.parts and p.is_file()
    }


def run_demo_watched(host: str, port: int, role: str, open_browser: bool) -> int:
    """Run the demo and restart it when the code changes, so a fix needs no manual stop/start.

    The open browser page reconnects by itself: the signing key is stable, so its token stays
    valid, and the page notices the new build and reloads. Use ``--no-watch`` for the lowest
    energy use (travel mode): then nothing runs besides the server.
    """
    if host not in LOOPBACK:
        sys.stderr.write("refused: demo mode only listens on the loopback address\n")
        return 2
    command = [
        sys.executable, "-m", "reggraph.cli", "demo",
        "--host", host, "--port", str(port), "--role", role, "--no-watch",
    ]  # fmt: skip
    child: subprocess.Popen[bytes] | None = None
    quick_failures = 0
    first = True
    try:
        while True:
            started = time.monotonic()
            before = _snapshot()
            args = command + (["--open"] if open_browser and first else [])
            child = subprocess.Popen(args)  # noqa: S603  # nosec B603
            first = False
            changed = False
            while child.poll() is None and not changed:
                time.sleep(WATCH_SECONDS)
                changed = _snapshot() != before
            if not changed:  # the server ended on its own (port busy, Ctrl+C in the child ...)
                if time.monotonic() - started < 5 and quick_failures < 5:
                    quick_failures += 1
                    time.sleep(1)
                    continue
                return int(child.returncode or 0)
            sys.stdout.write("Code changed - restarting the demo ...\n")
            sys.stdout.flush()
            child.terminate()
            child.wait(timeout=10)
    except KeyboardInterrupt:
        return 0
    finally:
        if child is not None and child.poll() is None:
            child.terminate()


def run_demo(host: str, port: int, role: str, open_browser: bool) -> int:
    if host not in LOOPBACK:
        sys.stderr.write("refused: demo mode only listens on the loopback address\n")
        return 2
    import uvicorn

    from reggraph.api import create_app

    settings = Settings(
        env="dev",
        token_keys={"demo": demo_key()},
        active_kid="demo",
        offline=True,
        rate_burst=60,
        rate_per_minute=600,
    )
    services = build_services(settings, SystemClock())
    people = seed(services, rich=True)
    who = people[role]
    token = services.signer.issue(who, ttl_seconds=TOKEN_HOURS * 3600)
    app = create_app(settings, SystemClock(), services=services)
    shown = "localhost" if host == "localhost" else host
    url = f"http://{shown}:{port}/dashboard#token={token}"
    sys.stdout.write(
        "RegGraph demo (offline, invented data, loopback only)\n"
        f"Open this link in Chrome (valid {TOKEN_HOURS} hours, role: {role}):\n\n{url}\n\n"
        "Stop with Ctrl+C. Data disappears when the program stops.\n"
    )
    sys.stdout.flush()
    if open_browser:
        webbrowser.open(url)
    # Low-power serving: one process, no reload, no polling, no per-request console output.
    uvicorn.run(
        app,
        host=host,
        port=port,
        server_header=False,
        log_level="warning",
        access_log=False,
        timeout_keep_alive=5,
    )
    return 0
