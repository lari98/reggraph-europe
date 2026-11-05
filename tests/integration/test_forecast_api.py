"""Forecast and advanced stress analysis through the HTTP stack, with security checks."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from reggraph.security.rbac import Role
from tests.integration.test_stress_api import put_scenario, run, scenario
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


@pytest.fixture
def made(app_client: TestClient, auth: Auth) -> dict[str, Any]:
    h = auth("ana", Role.ANALYST)
    register_all(app_client, h)
    set_rates(app_client, auth)
    assert put_contract(app_client, h, contract("P-1", INS, BANK, "1000")).status_code == 200
    loaded = app_client.post(
        "/v1/exposures/batches",
        headers=h,
        json=batch(
            pos("L1", BANK, CORP, "100"), pos("L2", BANK2, CORP, "60"), pos("L3", BANK2, BANK, "30")
        ),
    )
    assert loaded.status_code == 201, loaded.text
    body = scenario(
        "SC-1",
        {"kind": "default", "leis": [CORP], "loss_given_default_percent": "50"},
        insurance=False,
    )
    assert put_scenario(app_client, h, body).status_code == 200
    ran = run(app_client, h, capital_buffers={BANK: "20", BANK2: "10"})
    assert ran.status_code == 201, ran.text
    return {"h": h, "run": ran.json()["run"]["run_id"]}


def test_analysis_returns_ladder_reverse_test_and_cascade(
    app_client: TestClient, made: dict[str, Any]
) -> None:
    res = app_client.get(f"/v1/stress/runs/{made['run']}/analysis", headers=made["h"])
    assert res.status_code == 200, res.text
    a = res.json()
    assert [x["multiplier"] for x in a["ladder"]] == ["0.5", "1", "1.5", "2", "3", "4"]
    nets = [float(x["net_loss"]) for x in a["ladder"]]
    assert nets == sorted(nets) and a["currency"] == "EUR"
    assert {r["holder_lei"] for r in a["reverse"]} == {BANK, BANK2}
    assert a["system_multiplier"] is not None and a["cascade"][0]["round"] == 0


def test_forecast_shape_levers_and_holder_drill(
    app_client: TestClient, made: dict[str, Any]
) -> None:
    h, rid = made["h"], made["run"]
    base = app_client.get(f"/v1/forecast?run={rid}&scenario=severe&quarters=8", headers=h).json()
    assert len(base["series"]) == 9 and len(base["dates"]) == 9 and base["dates"][0] == "2026-03-02"
    assert {r["lei"] for r in base["holders"]} == {BANK, BANK2}
    assert base["summary"]["institutions"] == 2 and base["assumptions"]["shock_severity"] == 1.0
    again = app_client.get(f"/v1/forecast?run={rid}&scenario=severe&quarters=8", headers=h).json()
    assert again == base  # same seed, same answer
    helped = app_client.get(
        f"/v1/forecast?run={rid}&scenario=severe&quarters=8&raise_percent=100", headers=h
    ).json()
    assert helped["summary"]["end_p50"] > base["summary"]["end_p50"]
    solo = app_client.get(
        f"/v1/forecast?run={rid}&scenario=severe&quarters=8&holder={BANK}", headers=h
    ).json()
    assert len(solo["holders"]) == 1 and solo["holders"][0]["ledger"][1]["q"] == 2
    assert (
        solo["holders"][0]["bands"] == next(r for r in base["holders"] if r["lei"] == BANK)["bands"]
    )


def test_recovery_plan_compares_options(app_client: TestClient, made: dict[str, Any]) -> None:
    plan = app_client.get(
        f"/v1/forecast/plan?run={made['run']}&scenario=severe", headers=made["h"]
    ).json()
    keys = [o["key"] for o in plan["options"]]
    assert keys == ["none", "raise_early", "raise_late", "derisk", "combined"]
    none, early = plan["options"][0], plan["options"][1]
    assert (
        early["capital_raised"] > 0
        and early["institutions_at_risk"] <= none["institutions_at_risk"]
    )
    assert set(plan["needs"]) == {BANK, BANK2}


def test_forecast_refuses_bad_input_missing_rights_and_other_tenants(
    app_client: TestClient, auth: Auth, made: dict[str, Any]
) -> None:
    h, rid = made["h"], made["run"]
    assert app_client.get("/v1/forecast?run=999", headers=h).status_code == 404
    assert app_client.get(f"/v1/forecast?run={rid}").status_code == 401
    machine = auth("svc", Role.INGESTION_SERVICE)
    assert app_client.get(f"/v1/forecast?run={rid}", headers=machine).status_code == 403
    assert app_client.get(f"/v1/stress/runs/{rid}/analysis", headers=machine).status_code == 403
    for q in (
        "scenario=doom",
        "quarters=3",
        "quarters=99",
        "raise_percent=500",
        "derisk_percent=-1",
        "seed=-5",
        "run=0",
        "run=abc",
    ):
        assert (
            app_client.get(
                f"/v1/forecast?run={rid}&{q}".replace("run=" + str(rid) + "&run=", "run="),
                headers=h,
            ).status_code
            == 422
        ), q
    assert app_client.get(f"/v1/forecast?run={rid}&holder=NOTALEI", headers=h).status_code == 422
    assert (
        app_client.get(f"/v1/forecast?run={rid}&holder={CORP}", headers=h).status_code == 422
    )  # not a holder
    for hostile in ("x'%20OR%201=1--", "%3Cscript%3E", "..%2F..%2Fetc%2Fpasswd"):
        bad = app_client.get(f"/v1/forecast?run={rid}&holder={hostile}", headers=h)
        assert bad.status_code == 422 and "Traceback" not in bad.text
    other = auth("eve", Role.ANALYST, tenant="globex")
    assert app_client.get(f"/v1/forecast?run={rid}", headers=other).status_code == 404
    assert app_client.get(f"/v1/stress/runs/{rid}/analysis", headers=other).status_code == 404
