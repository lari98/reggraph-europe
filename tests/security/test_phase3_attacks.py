"""Hostile-input tests for exposure, insurance and connectivity (the "hacking" suite)."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reggraph.api.deps import Services
from reggraph.security.rbac import Role
from tests.conftest import make_lei
from tests.phase3 import (
    BANK,
    BANK2,
    CORP,
    INS,
    Auth,
    batch,
    contract,
    pos,
    put_contract,
    register_all,
    set_rates,
)

OTHER = "globex"


@pytest.fixture
def world(app_client: TestClient, auth: Auth) -> dict[str, str]:
    h = auth("ana", Role.ANALYST)
    register_all(app_client, h)
    set_rates(app_client, auth)
    assert put_contract(app_client, h, contract("P-1", INS, BANK, "1000")).status_code == 200
    resp = app_client.post(
        "/v1/exposures/batches", headers=h, json=batch(pos("L1", BANK, CORP, "10"))
    )
    assert resp.status_code == 201
    return h


ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/v1/exposures/batches"),
    ("GET", "/v1/exposures/batches/1"),
    ("GET", "/v1/exposures/edges"),
    ("GET", f"/v1/exposures/concentration/{BANK}"),
    ("GET", f"/v1/exposures/neighbourhood/{BANK}"),
    ("GET", "/v1/contracts"),
    ("GET", "/v1/contracts/P-1"),
    ("GET", "/v1/contracts/P-1/history"),
    ("GET", f"/v1/entities/{BANK}/insurance"),
    ("GET", f"/v1/entities/{BANK}/interconnection"),
    ("GET", f"/v1/entities/{BANK}/ecosystem"),
    ("POST", "/v1/exposures/batches"),
    ("PUT", "/v1/contracts/P-1"),
]


@pytest.mark.parametrize(("method", "url"), ENDPOINTS)
def test_authentication_required(
    app_client: TestClient, world: dict[str, str], method: str, url: str
) -> None:
    assert app_client.request(method, url, json={}).status_code == 401
    bad = {"Authorization": "Bearer v1.k1.forged.token"}
    assert app_client.request(method, url, headers=bad, json={}).status_code == 401


@pytest.mark.parametrize("role", [Role.VIEWER, Role.INGESTION_SERVICE])
@pytest.mark.parametrize(("method", "url"), ENDPOINTS)
def test_roles_without_permission_are_refused(
    app_client: TestClient, auth: Auth, world: dict[str, str], role: Role, method: str, url: str
) -> None:
    assert app_client.request(method, url, headers=auth("x", role), json={}).status_code == 403


def test_tenants_cannot_see_each_other(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    spy = auth("eve", Role.ANALYST, OTHER)
    assert app_client.get("/v1/exposures/batches", headers=spy).json() == []
    assert app_client.get("/v1/exposures/batches/1", headers=spy).status_code == 404
    assert app_client.get("/v1/contracts", headers=spy).json() == []
    assert app_client.get("/v1/contracts/P-1", headers=spy).status_code == 404
    assert app_client.get("/v1/contracts/P-1/history", headers=spy).status_code == 404
    edges = app_client.get("/v1/exposures/edges?on=2026-03-02", headers=spy).json()
    assert edges["edges"] == [] and edges["lineage"]["batch_ids"] == []
    eco = app_client.get(f"/v1/entities/{BANK}/ecosystem?on=2026-03-02&depth=4", headers=spy).json()
    assert eco["nodes"] == {BANK: 0} and eco["edges"] == []


def test_foreign_and_unknown_leis_look_identical(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    spy = auth("eve", Role.ANALYST, OTHER)
    foreign = app_client.post(
        "/v1/exposures/batches", headers=spy, json=batch(pos("Z", BANK, BANK2, "1"))
    )
    ghost = app_client.post(
        "/v1/exposures/batches",
        headers=spy,
        json=batch(pos("Z", make_lei("G1"), make_lei("G2"), "1")),
    )
    assert foreign.status_code == ghost.status_code == 422
    assert {**foreign.json(), "request_id": 0} == {**ghost.json(), "request_id": 0}
    assert BANK not in foreign.text
    c_foreign = put_contract(app_client, spy, contract("Z", INS, BANK, "1"))
    assert c_foreign.status_code == 422 and INS not in c_foreign.text


def test_duplicate_detection_does_not_leak_across_tenants(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    other = auth("root2", Role.ANALYST, OTHER)
    register_all(app_client, other)
    same = batch(pos("L1", BANK, CORP, "10"))
    assert app_client.post("/v1/exposures/batches", headers=other, json=same).status_code == 201
    assert app_client.get("/v1/exposures/batches", headers=world).json()[0]["batch_id"] == 1
    assert len(app_client.get("/v1/exposures/batches", headers=world).json()) == 1


def test_mass_assignment_is_rejected(app_client: TestClient, world: dict[str, str]) -> None:
    good = batch(pos("N1", BANK, CORP, "1"))
    for tweak in (
        {**good, "tenant_id": OTHER},
        {**good, "positions": [{**good["positions"][0], "batch_id": 99}]},
        {**good, "positions": [{**good["positions"][0], "position_id": 5}]},
        {**good, "loaded_by": "user:root"},
    ):
        assert (
            app_client.post("/v1/exposures/batches", headers=world, json=tweak).status_code == 422
        )
    body = {"contract": contract("P-7", INS, BANK, "1"), "change_summary": "x", "tenant_id": OTHER}
    assert app_client.put("/v1/contracts/P-7", headers=world, json=body).status_code == 422
    body = {"contract": {**contract("P-7", INS, BANK, "1"), "version_no": 9}, "change_summary": "x"}
    assert app_client.put("/v1/contracts/P-7", headers=world, json=body).status_code == 422


EVIL_TEXT = "'; DROP TABLE exposure_positions; --"


def test_sql_injection_is_inert(
    app_client: TestClient, services: Services, world: dict[str, str]
) -> None:
    bad_ref = batch(pos(EVIL_TEXT, BANK, CORP, "1"))
    assert app_client.post("/v1/exposures/batches", headers=world, json=bad_ref).status_code == 422
    # Free text may contain the characters, but only ever as bound data.
    ok = batch(pos("N2", BANK, CORP, "1"), source=EVIL_TEXT, desc="Robert'); DROP TABLE x;--")
    assert app_client.post("/v1/exposures/batches", headers=world, json=ok).status_code == 201
    for url in (
        f"/v1/contracts?after={EVIL_TEXT}",
        "/v1/contracts?insurer=' OR '1'='1",
        f"/v1/contracts/{EVIL_TEXT}",
        f"/v1/exposures/concentration/{EVIL_TEXT}",
        "/v1/exposures/edges?currency=EUR' OR 1=1",
        "/v1/exposures/batches?after=1;DROP TABLE exposure_batches",
    ):
        assert app_client.get(url, headers=world).status_code in {200, 400, 404, 422}, url
    assert services.db.query("SELECT COUNT(*) AS n FROM exposure_positions")[0]["n"] >= 2
    assert services.db.query("SELECT COUNT(*) AS n FROM insurance_contract_versions")[0]["n"] == 1


@pytest.mark.parametrize(
    "amount",
    [
        1.5,
        "1e6",
        "1E6",
        "0x10",
        "-0.01",
        "NaN",
        "Infinity",
        "-Infinity",
        "1,000",
        " 5",
        "5 ",
        "",
        None,
        [],
        {},
        True,
        "9" * 41,
        "1" + "0" * 15,
    ],
)
def test_malformed_amounts(app_client: TestClient, world: dict[str, str], amount: Any) -> None:
    resp = app_client.post(
        "/v1/exposures/batches",
        headers=world,
        json=batch({**pos("A1", BANK, CORP, "1"), "amount": amount}),
    )
    assert resp.status_code == 422, amount
    body = {**contract("C-9", INS, BANK, "1"), "sum_insured": amount}
    assert put_contract(app_client, world, body).status_code == 422, amount


def test_size_limits(app_client: TestClient, world: dict[str, str]) -> None:
    many = [pos(f"B{i}", BANK, CORP, "1") for i in range(1001)]
    assert app_client.post(
        "/v1/exposures/batches", headers=world, json=batch(*many)
    ).status_code in {413, 422}
    huge = batch(pos("H1", BANK, CORP, "1", source_ref="x" * 100_000))
    assert app_client.post("/v1/exposures/batches", headers=world, json=huge).status_code in {
        413,
        422,
    }
    empty = batch()
    assert app_client.post("/v1/exposures/batches", headers=world, json=empty).status_code == 422


def test_hostile_text_is_refused(app_client: TestClient, world: dict[str, str]) -> None:
    for text in ("evil‮txt", "zero​width", "nul\x00byte", "tab\tinside"):
        b = batch(pos("T1", BANK, CORP, "1"), desc=text)
        assert app_client.post("/v1/exposures/batches", headers=world, json=b).status_code == 422, (
            repr(text)
        )
        b = batch(pos("T1", BANK, CORP, "1", source_ref=text))
        assert app_client.post("/v1/exposures/batches", headers=world, json=b).status_code == 422, (
            repr(text)
        )
    body = {"contract": contract("P-8", INS, BANK, "1"), "change_summary": "bad‮"}
    assert app_client.put("/v1/contracts/P-8", headers=world, json=body).status_code == 422


def test_ids_in_urls_are_validated(app_client: TestClient, world: dict[str, str]) -> None:
    for url in (
        "/v1/exposures/batches/-1",
        "/v1/exposures/batches/abc",
        "/v1/exposures/batches/99999999999999999999",
        "/v1/exposures/edges?max_batch=-5",
        "/v1/exposures/edges?max_batch=99999999999",
        "/v1/exposures/neighbourhood/" + "A" * 500,
    ):
        assert app_client.get(url, headers=world).status_code in {404, 422}, url


def test_malformed_json_leaks_nothing(app_client: TestClient, world: dict[str, str]) -> None:
    resp = app_client.post(
        "/v1/exposures/batches",
        headers={**world, "Content-Type": "application/json"},
        content=b'{"positions": [}',
    )
    assert resp.status_code in {400, 422}
    for marker in ("Traceback", "sqlite", 'File "', "reggraph/"):
        assert marker not in resp.text


def test_database_refuses_tampering(
    services: Services, app_client: TestClient, world: dict[str, str]
) -> None:
    statements = [
        "UPDATE exposure_batches SET source_system = 'x'",
        "DELETE FROM exposure_batches",
        "UPDATE exposure_positions SET amount = '1'",
        "DELETE FROM exposure_positions",
        "UPDATE insurance_contract_versions SET line = 'life'",
        "DELETE FROM insurance_contract_versions",
    ]
    for sql in statements:
        with pytest.raises(sqlite3.DatabaseError):
            services.db.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):  # the table itself forbids self-exposure
        services.db.execute(
            "INSERT INTO exposure_positions (tenant_id, position_id, batch_id, exposure_ref, holder_lei,"
            " counterparty_lei, exposure_type, currency, amount, as_of, recorded_at)"
            " VALUES ('acme', 99, 1, 'S', ?, ?, 'loan', 'EUR', '1', '2026-01-01', 'now')",
            (BANK, BANK),
        )


def test_query_size_guard(
    app_client: TestClient, world: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    app_client.post(
        "/v1/exposures/batches",
        headers=world,
        json=batch(pos("Q2", BANK, CORP, "1"), pos("Q3", BANK, CORP, "1")),
    )
    monkeypatch.setattr("reggraph.exposure.store.MAX_QUERY_POSITIONS", 2)
    resp = app_client.get("/v1/exposures/edges?on=2026-03-02", headers=world)
    assert resp.status_code == 422  # refuse, do not silently truncate


def test_failed_load_leaves_no_partial_data(
    services: Services, app_client: TestClient, world: dict[str, str]
) -> None:
    before = services.db.query("SELECT COUNT(*) AS n FROM exposure_positions")[0]["n"]
    mixed = batch(pos("OK1", BANK, CORP, "1"), pos("BAD", BANK, make_lei("GHOST"), "1"))
    assert app_client.post("/v1/exposures/batches", headers=world, json=mixed).status_code == 422
    assert services.db.query("SELECT COUNT(*) AS n FROM exposure_positions")[0]["n"] == before
    assert app_client.get(
        "/v1/audit/verify", headers={"Authorization": world["Authorization"]}
    ).status_code in {200, 403}


def test_huge_ids_never_crash_the_server(app_client: TestClient, world: dict[str, str]) -> None:
    """Regression: an integer beyond SQLite's range used to surface as HTTP 500."""
    resp = app_client.post(
        "/v1/ownership/99999999999999999999/end",
        headers=world,
        json={"effective_until": "2026-01-01"},
    )
    assert resp.status_code == 422
    assert (
        app_client.get("/v1/exposures/batches/99999999999999999999", headers=world).status_code
        == 422
    )
