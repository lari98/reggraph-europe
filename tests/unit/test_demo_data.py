"""The large demo data set: valid, deterministic, German/Austrian/Swiss weighted, clearly labelled."""

from __future__ import annotations

import secrets
from collections import Counter

import pytest

from reggraph.api.deps import Services, build_services
from reggraph.config import Settings
from reggraph.core.clock import SystemClock
from reggraph.demo import seed
from reggraph.demo_data import _EXTRA, _GROUPS


def _services() -> Services:
    settings = Settings(
        env="dev",
        token_keys={"t": secrets.token_urlsafe(48).encode()},
        active_kid="t",
        offline=True,
    )
    return build_services(settings, SystemClock())


@pytest.fixture(scope="module")
def rich() -> Services:
    services = _services()
    seed(services, rich=True)
    return services


def test_names_are_unique_and_every_key_fits_a_lei_body() -> None:
    assert all(len(k) <= 18 and k.isalnum() and k == k.upper() for k in _EXTRA)
    assert len({v[0] for v in _EXTRA.values()}) == len(_EXTRA)
    assert all(p in _EXTRA and c in _EXTRA for p, c, _ in _GROUPS)


def test_rich_set_is_large_and_weighted_to_dach(rich: Services) -> None:
    rows = rich.db.query("SELECT jurisdiction, legal_name FROM entity_versions", ())
    countries = Counter(
        r["jurisdiction"]
        for r in rich.db.query("SELECT DISTINCT lei, jurisdiction FROM entity_versions", ())
    )
    assert sum(countries.values()) >= 70
    assert countries["DE"] >= 15 and countries["CH"] >= 8 and countries["AT"] >= 6
    assert rows and all("(demo)" in r["legal_name"] for r in rows)


def test_exposures_contracts_and_runs_are_plentiful(rich: Services) -> None:
    pos = rich.db.query("SELECT COUNT(*) AS n FROM exposure_positions", ())[0]["n"]
    runs = rich.db.query("SELECT COUNT(*) AS n FROM stress_runs", ())[0]["n"]
    assert pos >= 250 and runs >= 10


def test_same_data_on_every_start() -> None:
    def fingerprint() -> list[tuple[str, str]]:
        svc = _services()
        seed(svc, rich=True)
        rows = svc.db.query(
            "SELECT exposure_ref, amount FROM exposure_positions ORDER BY exposure_ref", ()
        )
        return [(r["exposure_ref"], str(r["amount"])) for r in rows]

    assert fingerprint() == fingerprint()


def test_small_set_is_unchanged_for_tests() -> None:
    services = _services()
    seed(services)
    assert services.db.query("SELECT COUNT(*) AS n FROM stress_runs", ())[0]["n"] == 3


def test_rules_have_real_references_and_every_status(rich: Services) -> None:
    rows = rich.db.query("SELECT status, COUNT(*) AS n FROM rule_versions GROUP BY status", ())
    by = {r["status"]: r["n"] for r in rows}
    for status in ("approved", "draft", "rejected", "superseded", "withdrawn"):
        assert by.get(status, 0) >= 1, status
    assert by["approved"] >= 15
    cites = rich.db.query("SELECT payload_json FROM rule_versions", ())
    text = " ".join(str(r["payload_json"]) for r in cites)
    for ref in ("575/2013", "2015/61", "2009/138/EC", "2022/2554", "SR 952.03"):
        assert ref in text, ref
    assert "illustrative" in text.lower() or "demo summary" in text.lower()
