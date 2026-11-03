"""Exposure graph through the real HTTP stack: loading, lineage, as-of logic, reproducibility."""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from reggraph.security.rbac import Role
from tests.phase3 import (
    BANK,
    BANK2,
    CORP,
    CORP2,
    INS,
    Auth,
    batch,
    pos,
    register_all,
    set_rates,
)


@pytest.fixture
def world(app_client: TestClient, auth: Auth) -> dict[str, str]:
    h = auth("ana", Role.ANALYST)
    register_all(app_client, h)
    set_rates(app_client, auth)
    first = batch(
        pos("L1", BANK, CORP, "100"),
        pos("L2", BANK, CORP2, "50", currency="USD"),  # 40.00 EUR at 1.25
        pos("L3", BANK, INS, "200", currency="PLN"),  # 50.00 EUR at 4.00
        pos("L4", BANK2, BANK, "30"),
    )
    resp = app_client.post("/v1/exposures/batches", headers=h, json=first)
    assert resp.status_code == 201, resp.text
    return h


def _get(client: TestClient, h: dict[str, str], url: str):  # type: ignore[no-untyped-def]
    return client.get(url, headers=h)


def test_batch_is_recorded_with_lineage(app_client: TestClient, world: dict[str, str]) -> None:
    body = app_client.get("/v1/exposures/batches/1", headers=world).json()
    assert body["position_count"] == 4 and body["source_system"] == "core-banking"
    assert body["loaded_by"] == "user:ana" and len(body["content_hash"]) == 64
    assert [
        b["batch_id"] for b in app_client.get("/v1/exposures/batches", headers=world).json()
    ] == [1]
    assert app_client.get("/v1/exposures/batches/99", headers=world).status_code == 404


def test_same_content_cannot_be_loaded_twice(app_client: TestClient, world: dict[str, str]) -> None:
    again = batch(
        pos("L1", BANK, CORP, "100"),
        pos("L2", BANK, CORP2, "50", currency="USD"),
        pos("L3", BANK, INS, "200", currency="PLN"),
        pos("L4", BANK2, BANK, "30"),
        source="another-system",
    )
    resp = app_client.post("/v1/exposures/batches", headers=world, json=again)
    assert resp.status_code == 409


def test_unregistered_lei_is_refused_without_naming_it(
    app_client: TestClient, world: dict[str, str]
) -> None:
    from tests.conftest import make_lei

    ghost = make_lei("GHOST")
    resp = app_client.post(
        "/v1/exposures/batches", headers=world, json=batch(pos("Z1", BANK, ghost, "1"))
    )
    assert resp.status_code == 422 and ghost not in resp.text
    assert (
        app_client.get("/v1/exposures/batches", headers=world).json()[-1]["batch_id"] == 1
    )  # nothing stored


def test_edges_are_converted_and_ordered(app_client: TestClient, world: dict[str, str]) -> None:
    body = _get(app_client, world, "/v1/exposures/edges?on=2026-03-02&currency=EUR").json()
    got = [(e["holder_lei"], e["counterparty_lei"], e["total"]) for e in body["edges"]]
    assert got == [
        (BANK, CORP, "100.00"),
        (BANK, INS, "50.00"),
        (BANK, CORP2, "40.00"),
        (BANK2, BANK, "30.00"),
    ]
    assert [f["currency"] for f in body["lineage"]["fx"]] == ["EUR", "PLN", "USD"]
    assert body["lineage"]["batch_ids"] == [1] and body["lineage"]["positions_used"] == 4
    assert len(body["result_hash"]) == 64


def test_reporting_in_another_currency(app_client: TestClient, world: dict[str, str]) -> None:
    body = _get(app_client, world, "/v1/exposures/edges?on=2026-03-02&currency=PLN").json()
    assert body["edges"][0]["total"] == "400.00"  # 100 EUR at 4.00


def test_missing_rate_stops_the_query(app_client: TestClient, world: dict[str, str]) -> None:
    resp = _get(app_client, world, "/v1/exposures/edges?on=2026-03-02&currency=SEK")
    assert resp.status_code == 422  # nothing guessed, nothing dropped
    stale = _get(app_client, world, "/v1/exposures/edges?on=2026-04-30&currency=EUR")
    assert stale.status_code == 422


def test_concentration_by_counterparty_and_group(
    app_client: TestClient, world: dict[str, str]
) -> None:
    url = f"/v1/exposures/concentration/{BANK}?on=2026-03-02&currency=EUR"
    plain = _get(app_client, world, url).json()
    assert plain["total"] == "190.00"
    assert [(s["key"], s["share"]) for s in plain["shares"]] == [
        (CORP, "0.5263"),
        (INS, "0.2632"),
        (CORP2, "0.2105"),
    ]
    assert Decimal(plain["hhi"]) == Decimal("3905.82") and plain["top1"] == "0.5263"
    # CORP owns 60 % of CORP2: by group the two merge under CORP.
    link = {"parent_lei": CORP, "child_lei": CORP2, "percent": "60", "effective_from": "2025-01-01"}
    assert app_client.post("/v1/ownership", headers=world, json=link).status_code == 201
    grouped = _get(app_client, world, url + "&by_group=true").json()
    assert [(s["key"], s["total"]) for s in grouped["shares"]] == [(CORP, "140.00"), (INS, "50.00")]
    assert Decimal(grouped["hhi"]) > Decimal(plain["hhi"])  # merging can only raise concentration


def test_latest_asof_maturity_and_corrections(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    set_rates(app_client, auth, "2026-02-26")  # rates must exist on or before the query day
    app_client.post(
        "/v1/exposures/batches",
        headers=world,
        json=batch(
            pos("L1", BANK, CORP, "80", as_of="2026-03-01"),
            pos("M1", BANK, CORP, "10", maturity_date="2026-02-28"),
        ),
    )
    edge = lambda day: {  # noqa: E731
        (e["holder_lei"], e["counterparty_lei"]): e["total"]
        for e in _get(app_client, world, f"/v1/exposures/edges?on={day}&currency=EUR").json()[
            "edges"
        ]
    }
    assert edge("2026-02-27")[(BANK, CORP)] == "110.00"  # L1 old value + M1
    assert edge("2026-02-28")[(BANK, CORP)] == "110.00"  # M1 still alive on its maturity day
    assert edge("2026-03-01")[(BANK, CORP)] == "80.00"  # L1 replaced, M1 matured
    app_client.post(  # same ref, same day, later load: the correction wins
        "/v1/exposures/batches",
        headers=world,
        json=batch(pos("L1", BANK, CORP, "85", as_of="2026-03-01")),
    )
    assert edge("2026-03-02")[(BANK, CORP)] == "85.00"


def test_queries_are_reproducible_with_max_batch(
    app_client: TestClient, world: dict[str, str]
) -> None:
    url = "/v1/exposures/edges?on=2026-03-02&currency=EUR"
    before = _get(app_client, world, url + "&max_batch=1").json()
    assert _get(app_client, world, url + "&max_batch=1").json() == before  # repeatable
    app_client.post(
        "/v1/exposures/batches",
        headers=world,
        json=batch(pos("N1", BANK, CORP, "999", as_of="2026-03-02")),
    )
    assert (
        _get(app_client, world, url + "&max_batch=1").json() == before
    )  # newer data changes nothing
    now = _get(app_client, world, url).json()
    assert now["result_hash"] != before["result_hash"] and now["lineage"]["batch_ids"] == [1, 2]


def test_neighbourhood(app_client: TestClient, world: dict[str, str]) -> None:
    body = _get(
        app_client, world, f"/v1/exposures/neighbourhood/{CORP}?on=2026-03-02&depth=2"
    ).json()
    assert body["nodes"] == {CORP: 0, BANK: 1, BANK2: 2, CORP2: 2, INS: 2}
    assert body["truncated"] is False and len(body["edges"]) == 4
    assert _get(app_client, world, f"/v1/exposures/neighbourhood/{CORP}?depth=9").status_code == 422
    assert _get(
        app_client, world, f"/v1/exposures/neighbourhood/{CORP}?depth=0&on=2026-03-02"
    ).json()["nodes"] == {CORP: 0}


def test_bad_query_parameters(app_client: TestClient, world: dict[str, str]) -> None:
    for url in (
        "/v1/exposures/edges?currency=EU",
        "/v1/exposures/edges?currency=XXX",
        "/v1/exposures/edges?on=not-a-date",
        "/v1/exposures/edges?max_batch=0",
        "/v1/exposures/concentration/NOTALEI",
        "/v1/exposures/batches?limit=0",
    ):
        assert _get(app_client, world, url).status_code in {400, 422}, url


def test_audit_chain_covers_loading(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    admin = auth("root", Role.TENANT_ADMIN)
    assert app_client.get("/v1/audit/verify", headers=admin).json()["ok"] is True
    actions = [e["action"] for e in app_client.get("/v1/audit", headers=admin).json()]
    assert "exposure.batch_loaded" in actions
