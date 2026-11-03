"""Hostile-input and abuse tests for the stress lab."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from reggraph.api.deps import Services
from reggraph.security.rbac import Role
from tests.conftest import DB_ERROR, make_lei
from tests.integration.test_stress_api import put_scenario, run, scenario
from tests.phase3 import (
    BANK,
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
    put_contract(app_client, h, contract("P-1", INS, BANK, "1000"))
    app_client.post("/v1/exposures/batches", headers=h, json=batch(pos("L1", BANK, CORP, "100")))
    assert put_scenario(app_client, h, scenario()).status_code == 200
    assert run(app_client, h).status_code == 201
    return h


ENDPOINTS = [
    ("PUT", "/v1/stress/scenarios/SC-1"),
    ("GET", "/v1/stress/scenarios"),
    ("GET", "/v1/stress/scenarios/SC-1"),
    ("GET", "/v1/stress/scenarios/SC-1/history"),
    ("POST", "/v1/stress/runs"),
    ("GET", "/v1/stress/runs"),
    ("GET", "/v1/stress/runs/1"),
    ("GET", "/v1/stress/runs/1/snapshot"),
    ("POST", "/v1/stress/runs/1/verify"),
]


@pytest.mark.parametrize(("method", "url"), ENDPOINTS)
def test_authentication_required(
    app_client: TestClient, world: dict[str, str], method: str, url: str
) -> None:
    assert app_client.request(method, url, json={}).status_code == 401
    forged = {"Authorization": "Bearer v1.k1.forged.token"}
    assert app_client.request(method, url, headers=forged, json={}).status_code == 401


@pytest.mark.parametrize("role", [Role.VIEWER, Role.INGESTION_SERVICE])
@pytest.mark.parametrize(("method", "url"), ENDPOINTS)
def test_roles_without_permission_are_refused(
    app_client: TestClient, auth: Auth, world: dict[str, str], role: Role, method: str, url: str
) -> None:
    assert app_client.request(method, url, headers=auth("x", role), json={}).status_code == 403


def test_tenants_are_isolated(app_client: TestClient, auth: Auth, world: dict[str, str]) -> None:
    spy = auth("eve", Role.ANALYST, OTHER)
    assert app_client.get("/v1/stress/scenarios", headers=spy).json() == []
    assert app_client.get("/v1/stress/scenarios/SC-1", headers=spy).status_code == 404
    assert app_client.get("/v1/stress/runs", headers=spy).json() == []
    for url, method in (
        ("/v1/stress/runs/1", "GET"),
        ("/v1/stress/runs/1/snapshot", "GET"),
        ("/v1/stress/runs/1/verify", "POST"),
    ):
        assert app_client.request(method, url, headers=spy).status_code == 404
    assert run(app_client, spy).status_code == 404  # cannot run another tenant's scenario


def test_foreign_scenario_leis_look_like_unknown_ones(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    spy = auth("eve", Role.ANALYST, OTHER)
    shock = lambda lei: [{"kind": "default", "leis": [lei], "loss_given_default_percent": "10"}]  # noqa: E731
    put_scenario(app_client, spy, scenario("X1", *shock(CORP)))
    put_scenario(app_client, spy, scenario("X2", *shock(make_lei("GHOST"))))
    a, b = run(app_client, spy, "X1"), run(app_client, spy, "X2")
    assert a.status_code == b.status_code == 422
    assert {**a.json(), "request_id": 0} == {**b.json(), "request_id": 0} and CORP not in a.text


def test_mass_assignment(app_client: TestClient, world: dict[str, str]) -> None:
    body = {"scenario": scenario("MA"), "change_summary": "x", "tenant_id": OTHER}
    assert app_client.put("/v1/stress/scenarios/MA", headers=world, json=body).status_code == 422
    for extra in (
        {"run_id": 99},
        {"signature": "k1.00"},
        {"input_hash": "0" * 64},
        {"tenant_id": OTHER},
        {"created_by": "root"},
    ):
        resp = app_client.post(
            "/v1/stress/runs", headers=world, json={"scenario_key": "SC-1", **extra}
        )
        assert resp.status_code == 422, extra


@pytest.mark.parametrize(
    "percent",
    [
        "1e2",
        "1E1",
        "NaN",
        "Infinity",
        "-1",
        "0",
        "100.01",
        "101",
        " 5",
        "5 ",
        "0x10",
        "",
        None,
        [],
        {},
        5.5,
        True,
        "9" * 400,
    ],
)
def test_percent_abuse(app_client: TestClient, world: dict[str, str], percent: Any) -> None:
    shock = {"kind": "default", "leis": [CORP], "loss_given_default_percent": percent}
    assert put_scenario(app_client, world, scenario("PA", shock)).status_code == 422


def test_scenario_size_limits(app_client: TestClient, world: dict[str, str]) -> None:
    shock = {"kind": "haircut", "percent": "1", "exposure_type": "loan"}
    assert put_scenario(app_client, world, scenario("BIG", *[shock] * 51)).status_code == 422
    many = {"kind": "default", "leis": [CORP] * 51, "loss_given_default_percent": "1"}
    assert put_scenario(app_client, world, scenario("BIG", many)).status_code == 422
    huge = scenario("BIG", shock)
    huge["description"] = "x" * 100_000
    assert put_scenario(app_client, world, huge).status_code in {413, 422}


def test_buffer_abuse(app_client: TestClient, world: dict[str, str]) -> None:
    for bad in (
        {"NOTALEI": "1"},
        {BANK: "1e9"},
        {BANK: "-5"},
        {BANK: 1.5},
        {BANK: ""},
        {BANK: "9" * 41},
    ):
        assert run(app_client, world, capital_buffers=bad).status_code == 422, bad
    flood = {make_lei(f"B{i:04d}"): "1" for i in range(201)}
    assert run(app_client, world, capital_buffers=flood).status_code == 422


def test_injection_and_ids(
    app_client: TestClient, services: Services, world: dict[str, str]
) -> None:
    evil = "'; DROP TABLE stress_runs; --"
    for url in (
        f"/v1/stress/scenarios/{evil}",
        f"/v1/stress/scenarios/{evil}/history",
        f"/v1/stress/runs?scenario_key={evil}",
        f"/v1/stress/scenarios?after={evil}",
    ):
        assert app_client.get(url, headers=world).status_code in {200, 404, 422}, url
    assert run(app_client, world, evil).status_code in {404, 422}
    for url in (
        "/v1/stress/runs/0",
        "/v1/stress/runs/-1",
        "/v1/stress/runs/abc",
        "/v1/stress/runs/99999999999999999999",
    ):
        assert app_client.get(url, headers=world).status_code == 422, url
    assert (
        app_client.post("/v1/stress/runs/99999999999999999999/verify", headers=world).status_code
        == 422
    )
    assert services.db.query("SELECT COUNT(*) AS n FROM stress_runs")[0]["n"] == 1


def test_bad_run_parameters(app_client: TestClient, world: dict[str, str]) -> None:
    for over in (
        {"currency": "EU"},
        {"currency": "EUR' OR 1=1"},
        {"max_batch": -1},
        {"max_batch": 99999999999},
        {"scenario_version": 0},
        {"as_of": "2026-13-45"},
    ):
        assert run(app_client, world, **over).status_code == 422, over
    assert run(app_client, world, scenario_version=7).status_code == 404


def test_history_cannot_be_rewritten(
    app_client: TestClient, services: Services, world: dict[str, str]
) -> None:
    for sql in (
        "UPDATE stress_scenario_versions SET payload_json = '{}'",
        "DELETE FROM stress_scenario_versions",
        "UPDATE stress_runs SET result_json = '{}'",
        "DELETE FROM stress_runs",
    ):
        with pytest.raises(DB_ERROR):
            services.db.execute(sql)


def test_a_signature_cannot_be_moved_to_another_run(
    app_client: TestClient, services: Services, world: dict[str, str]
) -> None:
    run(app_client, world)
    sig1 = services.db.query("SELECT signature FROM stress_runs WHERE run_id = 1")[0]["signature"]
    from tests.conftest import drop_trigger

    drop_trigger(services.db, "run_immutable", "stress_runs")
    services.db.execute("UPDATE stress_runs SET signature = ? WHERE run_id = 2", (sig1,))
    checked = app_client.post("/v1/stress/runs/2/verify", headers=world).json()
    assert checked["signature_ok"] is False and checked["ok"] is False


def test_failed_run_stores_nothing(
    app_client: TestClient, services: Services, world: dict[str, str]
) -> None:
    before = services.db.query("SELECT COUNT(*) AS n FROM stress_runs")[0]["n"]
    assert run(app_client, world, currency="SEK").status_code == 422
    assert services.db.query("SELECT COUNT(*) AS n FROM stress_runs")[0]["n"] == before
