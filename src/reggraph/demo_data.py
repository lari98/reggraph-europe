"""A larger, realistic-looking demo data set (all names and figures are invented).

``reggraph demo`` loads this on top of the small core set in :mod:`reggraph.demo`, so the dashboard
shows a believable picture: about seventy institutions, weighted towards Germany, Austria and
Switzerland, with several hundred exposures, insurance contracts, ownership groups and a series of
stress runs that give the trend chart something to draw. Nothing here is real: every name carries
"(demo)", every figure comes from a seeded random generator (same result on every start), and the
exchange rates are invented.

The generator is deterministic on purpose, so screenshots and talks always show the same data.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from reggraph.exposure import BatchInput
from reggraph.insurance import ContractInput
from reggraph.registry import EntityInput, OwnershipInput
from reggraph.stress import ScenarioInput

if TYPE_CHECKING:  # pragma: no cover - typing only
    from reggraph.api.deps import Services
    from reggraph.security.rbac import Principal

# key -> (name, type, country, currency); keys are at most 18 characters (they become LEI bodies)
_EXTRA: dict[str, tuple[str, str, str, str]] = {
    # Germany
    "ISARBANK": ("Isar Bank AG", "bank", "DE", "EUR"),
    "ELBEKREDIT": ("Elbe Kreditbank AG", "bank", "DE", "EUR"),
    "RUHRLAND": ("Ruhrland Bank AG", "bank", "DE", "EUR"),
    "SCHWARZWALD": ("Schwarzwald Volksbank eG", "bank", "DE", "EUR"),
    "HANSEATIC": ("Hanseatische Handelsbank AG", "bank", "DE", "EUR"),
    "MAINFIN": ("Main Finanzgruppe AG", "bank", "DE", "EUR"),
    "BAVARIALEBEN": ("Bavaria Lebensversicherung AG", "insurer", "DE", "EUR"),
    "NORDSACH": ("Norddeutsche Sachversicherung AG", "insurer", "DE", "EUR"),
    "SUEDRUECK": ("Süddeutsche Rück AG", "reinsurer", "DE", "EUR"),
    "STAHLRUHR": ("Stahlwerk Ruhr AG", "corporate", "DE", "EUR"),
    "NECKARAUTO": ("Neckar Automotive GmbH", "corporate", "DE", "EUR"),
    "CHEMIERHEIN": ("Chemie Rhein AG", "corporate", "DE", "EUR"),
    "WINDNORDSEE": ("Windkraft Nordsee GmbH", "corporate", "DE", "EUR"),
    "ALLGAEUMASCH": ("Maschinenbau Allgäu AG", "corporate", "DE", "EUR"),
    "LOGHANSE": ("Logistik Hanse GmbH", "corporate", "DE", "EUR"),
    "PHARMAISAR": ("Pharma Isartal AG", "corporate", "DE", "EUR"),
    "RHEINKAPITAL": ("Rheinkapital Asset Management GmbH", "fund_manager", "DE", "EUR"),
    "FFMWERT": ("Frankfurter Wertpapierhaus AG", "investment_firm", "DE", "EUR"),
    "MAINHLD": ("Main Beteiligungs AG", "holding", "DE", "EUR"),
    # Austria
    "DONAUBANK": ("Donaubank AG", "bank", "AT", "EUR"),
    "ALPENKREDIT": ("Alpenländische Kreditanstalt AG", "bank", "AT", "EUR"),
    "STEIRBANK": ("Steirische Landesbank AG", "bank", "AT", "EUR"),
    "BELVEDERE": ("Belvedere Versicherung AG", "insurer", "AT", "EUR"),
    "TAUERNSTAHL": ("Tauern Stahl AG", "corporate", "AT", "EUR"),
    "SALZACHPAP": ("Salzach Papier AG", "corporate", "AT", "EUR"),
    "ARLBERGBAHN": ("Arlberg Seilbahnen AG", "corporate", "AT", "EUR"),
    # Switzerland
    "ZUERICHSEE": ("Zürichsee Privatbank AG", "bank", "CH", "CHF"),
    "SAENTIS": ("Säntis Bank AG", "bank", "CH", "CHF"),
    "GENFERSEE": ("Genfersee Banque SA", "bank", "CH", "CHF"),
    "TICINO": ("Ticino Credito SA", "bank", "CH", "CHF"),
    "BERNINA": ("Bernina Versicherungen AG", "insurer", "CH", "CHF"),
    "JUNGFRAURUECK": ("Jungfrau Rück AG", "reinsurer", "CH", "CHF"),
    "UHRJURA": ("Uhrwerk Jura AG", "corporate", "CH", "CHF"),
    "PHARMABASEL": ("Pharma Basel Chemie AG", "corporate", "CH", "CHF"),
    "SCHOKOEMMENTAL": ("Schokolade Emmental AG", "corporate", "CH", "CHF"),
    "RHEINTALMASCH": ("Rheintal Maschinen AG", "corporate", "CH", "CHF"),
    "RHEINTALBANK": ("Rheintal Privatbank AG", "bank", "CH", "CHF"),
    # Rest of Europe
    "MOSELLEFONDS": ("Moselle Fonds Bank SA", "bank", "LU", "EUR"),
    "GRANDDUCHE": ("Grand-Duché Asset Management SA", "fund_manager", "LU", "EUR"),
    "LOIREBANQUE": ("Loire Banque SA", "bank", "FR", "EUR"),
    "PROVENCEASS": ("Provence Assurances SA", "insurer", "FR", "EUR"),
    "ATLANTAERO": ("Atlantique Aéro SA", "corporate", "FR", "EUR"),
    "ADRIATICA": ("Adriatica Banca S.p.A.", "bank", "IT", "EUR"),
    "TIRRENA": ("Tirrena Assicurazioni S.p.A.", "insurer", "IT", "EUR"),
    "PADANAENERGIA": ("Padana Energia S.p.A.", "corporate", "IT", "EUR"),
    "IBERIABANCO": ("Iberia Banco S.A.", "bank", "ES", "EUR"),
    "LEVANTESEG": ("Levante Seguros S.A.", "insurer", "ES", "EUR"),
    "COSTASOLAR": ("Costa Solar S.A.", "corporate", "ES", "EUR"),
    "HOLLANDBANK": ("Hollandse Bank N.V.", "bank", "NL", "EUR"),
    "DELTAHAVEN": ("Delta Havens N.V.", "corporate", "NL", "EUR"),
    "BRABANT": ("Brabant Bank N.V.", "bank", "BE", "EUR"),
    "LIFFEYRE": ("Liffey Reinsurance DAC", "reinsurer", "IE", "EUR"),
    "NORDICKREDIT": ("Nordic Kredit AB", "bank", "SE", "SEK"),
    "KATTEGAT": ("Kattegat Bank A/S", "bank", "DK", "DKK"),
    "MAZOVIA": ("Mazovia Bank S.A.", "bank", "PL", "PLN"),
    "VLTAVA": ("Vltava Banka a.s.", "bank", "CZ", "CZK"),
    "THAMESMERCH": ("Thames Merchant Bank plc", "bank", "GB", "GBP"),
    "ALBIONENERGY": ("Albion Energy plc", "corporate", "GB", "GBP"),
    "HUDSONCAP": ("Hudson Capital Corp", "bank", "US", "USD"),
    "PACIFICFREIGHT": ("Pacific Freight Inc", "corporate", "US", "USD"),
    "MERLIONTRADE": ("Merlion Trade Bank Ltd", "bank", "SG", "SGD"),
}
EXTRA_RATES = {"SEK": "11.2000", "DKK": "7.4600", "CZK": "24.3000"}

# (parent, child, percent): a few believable group structures
_GROUPS = (
    ("MAINHLD", "MAINFIN", "100"),
    ("MAINHLD", "NECKARAUTO", "51"),
    ("RHEINKAPITAL", "LOGHANSE", "27.5"),
    ("BAVARIALEBEN", "SUEDRUECK", "40"),
    ("BELVEDERE", "ALPENKREDIT", "18"),
    ("ZUERICHSEE", "RHEINTALBANK", "100"),
    ("BERNINA", "JUNGFRAURUECK", "60"),
    ("GRANDDUCHE", "MOSELLEFONDS", "100"),
    ("STAHLRUHR", "TAUERNSTAHL", "35"),
    ("HOLLANDBANK", "BRABANT", "75"),
)
_TYPES_FOR = {
    "corporate": ("loan", "loan", "bond", "guarantee"),
    "bank": ("deposit", "repo", "derivative", "bond"),
    "insurer": ("bond", "derivative"),
    "reinsurer": ("bond", "derivative"),
    "holding": ("loan", "bond"),
    "fund_manager": ("bond", "derivative"),
    "investment_firm": ("bond", "derivative"),
}
_DACH = {"DE", "AT", "CH", "LI"}


def _amount(rng: random.Random) -> str:
    """A rounded amount between 15 and 700 million, mostly small, sometimes large."""
    value = math.exp(rng.uniform(math.log(15e6), math.log(700e6)))
    return str(int(round(value, -5)))


def _positions(
    rng: random.Random, names: dict[str, tuple[str, str, str, str]], day: date
) -> list[dict[str, Any]]:
    """Several hundred exposures: every bank lends to a mix of home-market and foreign names."""
    keys = sorted(names)
    holders = [k for k in keys if names[k][1] == "bank"]
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for holder in holders:
        home = names[holder][2]
        wanted = rng.randint(6, 11)
        tries = 0
        while wanted and tries < 200:
            tries += 1
            near = [
                k
                for k in keys
                if k != holder and names[k][2] in (_DACH if home in _DACH else {home})
            ]
            pool = near if rng.random() < 0.55 and near else [k for k in keys if k != holder]
            cp = rng.choice(pool)
            kind = rng.choice(_TYPES_FOR.get(names[cp][1], ("loan",)))
            if (holder, cp, kind) in seen:
                continue
            seen.add((holder, cp, kind))
            wanted -= 1
            out.append(
                {
                    "holder": holder,
                    "cp": cp,
                    "type": kind,
                    "currency": names[cp][3],
                    "amount": _amount(rng),
                    "as_of": day.isoformat(),
                }
            )
    return out


def extend(
    services: Services, people: dict[str, Principal], leis: dict[str, str], today: date
) -> None:
    """Add the large demo set. ``leis`` is the key -> LEI map of the core data and is extended."""
    analyst, admin = people["analyst"], people["admin"]
    from reggraph.demo import make_lei  # late import: demo imports this module

    for key in _EXTRA:
        leis[key] = make_lei(key)
    for key, (name, kind, country, cur) in _EXTRA.items():
        services.registry.upsert(
            analyst,
            EntityInput.model_validate(
                {
                    "lei": leis[key],
                    "legal_name": f"{name} (demo)",
                    "entity_type": kind,
                    "jurisdiction": country,
                    "reporting_currency": cur,
                    "reporting_basis": "consolidated"
                    if kind in {"bank", "holding"}
                    else "individual",
                }
            ),
            "demo data (large set)",
        )
    for parent, child, pct in _GROUPS:
        services.registry.link(
            analyst,
            OwnershipInput.model_validate(
                {
                    "parent_lei": leis[parent],
                    "child_lei": leis[child],
                    "percent": pct,
                    "effective_from": "2019-01-01",
                }
            ),
        )
    services.ratebook.put(admin, today, "demo rates (invented, not market data)", EXTRA_RATES)

    # fixed seed on purpose: repeatable demo data, not security
    rng = random.Random(2026)  # noqa: S311  # nosec B311
    from reggraph.demo import _ENTITIES  # the core institutions can be counterparties too

    names = {**_ENTITIES, **_EXTRA}
    rows = _positions(rng, names, today - timedelta(days=1))
    batch = [
        {
            "exposure_ref": f"DEMO-L{i:04d}",
            "holder_lei": leis[r["holder"]],
            "counterparty_lei": leis[r["cp"]],
            "exposure_type": r["type"],
            "currency": r["currency"],
            "amount": r["amount"],
            "as_of": r["as_of"],
        }
        for i, r in enumerate(rows, 1)
    ]
    for start in range(0, len(batch), 400):
        services.exposures.load_batch(
            analyst,
            BatchInput.model_validate(
                {
                    "source_system": "demo-generator",
                    "description": "invented (large set)",
                    "positions": batch[start : start + 400],
                }
            ),
        )

    insurers = [k for k in sorted(names) if names[k][1] == "insurer"]
    insured = [k for k in sorted(names) if names[k][1] in {"bank", "corporate"}]
    lines = ("non_life", "life", "credit_and_surety", "health", "non_life")
    for n in range(1, 41):
        ins, cov = rng.choice(insurers), rng.choice(insured)
        services.contracts.upsert(
            analyst,
            ContractInput.model_validate(
                {
                    "contract_ref": f"D-L-{n:03d}",
                    "insurer_lei": leis[ins],
                    "insured_lei": leis[cov],
                    "line": rng.choice(lines),
                    "currency": names[ins][3],
                    "sum_insured": str(int(round(rng.uniform(40e6, 600e6), -6))),
                    "start_date": "2025-01-01",
                }
            ),
            "demo data (large set)",
        )

    # scenarios: country shocks, a currency move, a failed insurer, and a drifting rating series
    def leis_of(*keys: str) -> list[str]:
        return [leis[k] for k in keys]

    scenarios: list[tuple[str, list[dict[str, Any]]]] = [
        (
            "DEMO-DE-INDUSTRY",
            [
                {
                    "kind": "default",
                    "leis": leis_of("STAHLRUHR", "NECKARAUTO", "CHEMIERHEIN"),
                    "loss_given_default_percent": "40",
                }
            ],
        ),
        (
            "DEMO-AT-DEFAULT",
            [
                {
                    "kind": "default",
                    "leis": leis_of("TAUERNSTAHL", "SALZACHPAP"),
                    "loss_given_default_percent": "50",
                }
            ],
        ),
        ("DEMO-CH-FRANC", [{"kind": "fx", "currency": "CHF", "change_percent": "15"}]),
        ("DEMO-GBP-FALL", [{"kind": "fx", "currency": "GBP", "change_percent": "-15"}]),
        (
            "DEMO-INSURER-DE",
            [
                {"kind": "insurer_failure", "leis": leis_of("NORDSACH")},
                {
                    "kind": "default",
                    "leis": leis_of("WINDNORDSEE"),
                    "loss_given_default_percent": "55",
                },
            ],
        ),
    ]
    buffers = {}
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["holder"]] = totals.get(r["holder"], 0.0) + float(r["amount"])
    for holder, total in totals.items():
        buffers[leis[holder]] = str(int(total * rng.uniform(0.06, 0.16)))

    def run(key: str, shocks: list[dict[str, Any]], title: str) -> None:
        services.stress_store.upsert_scenario(
            analyst,
            ScenarioInput.model_validate(
                {
                    "scenario_key": key,
                    "title": title,
                    "description": "demo scenario",
                    "shocks": shocks,
                    "use_insurance": True,
                }
            ),
            "demo data (large set)",
        )
        services.stress.run(analyst, key, on=today, currency="EUR", buffers=buffers)

    for key, shocks in scenarios:
        run(key, shocks, key.replace("-", " ").title())
    for step, lgd in enumerate(("20", "30", "40", "50", "60"), 1):  # the trend series
        run(
            "DEMO-DRIFT",
            [
                {
                    "kind": "default",
                    "leis": leis_of("KRONOSIND", "STAHLRUHR", "PADANAENERGIA", "ALBIONENERGY"),
                    "loss_given_default_percent": lgd,
                }
            ],
            f"Demo drift step {step}",
        )

    now = services.clock.now().isoformat()
    for n, title in enumerate(
        (
            "Demo alert: consultation on operational resilience (sample)",
            "Demo alert: updated reporting templates (sample)",
            "Demo alert: supervisory statement on concentration risk (sample)",
            "Demo alert: amended capital buffer guidance (sample)",
        ),
        3,
    ):
        services.db.execute(
            "INSERT INTO source_alerts (tenant_id, alert_id, source, item_key, title, url,"
            " published, status, created_at) VALUES (?, ?, 'eurlex', ?, ?, ?, NULL, 'open', ?)",
            ("demo", n, f"demo:{n}", title, "https://eur-lex.europa.eu/", now),
        )
