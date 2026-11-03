"""Stress lab through the HTTP stack: scenarios, runs, determinism, signatures, verification."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from reggraph.api.deps import Services
from reggraph.security.rbac import Role
from tests.conftest import drop_trigger
from tests.phase3 import (
    BANK,
    BANK2,
    CORP,
    CORP2,
    INS,
    Auth,
    batch,
    contract,
    pos,
    put_contract,
    register_all,
    set_rates,
)


def scenario(key: str = "SC-1", *shocks: dict[str, Any], insurance: bool = True) -> dict[str, Any]:
    return {
        "scenario_key": key,
        "title": "Corporate default",
        "description": "test",
        "shocks": list(shocks)
        or [{"kind": "default", "leis": [CORP], "loss_given_default_percent": "50"}],
        "use_insurance": insurance,
    }


def put_scenario(client: TestClient, h: dict[str, str], body: dict[str, Any], note: str = "new"):  # type: ignore[no-untyped-def]
    return client.put(
        f"/v1/stress/scenarios/{body['scenario_key']}",
        headers=h,
        json={"scenario": body, "change_summary": note},
    )


def run(client: TestClient, h: dict[str, str], key: str = "SC-1", **over: Any):  # type: ignore[no-untyped-def]
    body = {"scenario_key": key, "as_of": "2026-03-02", **over}
    return client.post("/v1/stress/runs", headers=h, json=body)


@pytest.fixture
def world(app_client: TestClient, auth: Auth) -> dict[str, str]:
    h = auth("ana", Role.ANALYST)
    register_all(app_client, h)
    set_rates(app_client, auth)
    assert put_contract(app_client, h, contract("P-1", INS, BANK, "1000")).status_code == 200
    loaded = app_client.post(
        "/v1/exposures/batches",
        headers=h,
        json=batch(
            pos("L1", BANK, CORP, "100"),
            pos("L2", BANK, CORP2, "50", currency="USD"),  # 40.00 EUR
            pos("L3", BANK, INS, "200", currency="PLN", exposure_type="bond"),  # 50.00 EUR
            pos("L4", BANK2, BANK, "30"),
        ),
    )
    assert loaded.status_code == 201, loaded.text
    assert put_scenario(app_client, h, scenario()).status_code == 200
    return h


def test_scenario_lifecycle(app_client: TestClient, world: dict[str, str]) -> None:
    changed = scenario(
        "SC-1", {"kind": "default", "leis": [CORP], "loss_given_default_percent": "60"}
    )
    assert put_scenario(app_client, world, changed, "harsher").json()["version_no"] == 2
    assert put_scenario(app_client, world, changed).status_code == 409
    history = app_client.get("/v1/stress/scenarios/SC-1/history", headers=world).json()
    assert [v["version_no"] for v in history] == [1, 2]
    old = app_client.get("/v1/stress/scenarios/SC-1?version=1", headers=world).json()
    assert old["scenario"]["shocks"][0]["loss_given_default_percent"] == "50"
    assert app_client.get("/v1/stress/scenarios/SC-1?version=9", headers=world).status_code == 404
    assert app_client.get("/v1/stress/scenarios/NOPE", headers=world).status_code == 404
    assert [
        s["scenario_key"] for s in app_client.get("/v1/stress/scenarios", headers=world).json()
    ] == ["SC-1"]
    mismatch = {"scenario": scenario("OTHER"), "change_summary": "x"}
    assert (
        app_client.put("/v1/stress/scenarios/SC-1", headers=world, json=mismatch).status_code == 422
    )


def test_run_computes_signs_and_verifies(app_client: TestClient, world: dict[str, str]) -> None:
    resp = run(app_client, world)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    h = next(x for x in body["result"]["holders"] if x["holder_lei"] == BANK)
    # CORP is 100 EUR; 50 % lost. INS cover of 1 000 absorbs it fully.
    assert (h["gross_loss"], h["insurance_recovery"], h["net_loss"]) == ("50.00", "50.00", "0.00")
    assert body["run"]["signature"].startswith("k1.") and body["run"]["max_batch"] == 1
    checked = app_client.post(
        f"/v1/stress/runs/{body['run']['run_id']}/verify", headers=world
    ).json()
    assert checked["ok"] is True and all(v for k, v in checked.items() if k != "run_id")


def test_insurer_failure_removes_the_recovery(
    app_client: TestClient, world: dict[str, str]
) -> None:
    fail = scenario(
        "SC-2",
        {"kind": "default", "leis": [CORP], "loss_given_default_percent": "50"},
        {"kind": "insurer_failure", "leis": [INS]},
    )
    assert put_scenario(app_client, world, fail).status_code == 200
    body = run(app_client, world, "SC-2").json()
    h = next(x for x in body["result"]["holders"] if x["holder_lei"] == BANK)
    assert (h["insurance_recovery"], h["net_loss"], h["net_loss_ratio"]) == (
        "0.00",
        "50.00",
        "0.2632",
    )
    assert body["result"]["totals"]["net_loss"] == "50.00"


def test_group_default_fx_and_haircut(app_client: TestClient, world: dict[str, str]) -> None:
    link = {"parent_lei": CORP, "child_lei": CORP2, "percent": "60", "effective_from": "2025-01-01"}
    assert app_client.post("/v1/ownership", headers=world, json=link).status_code == 201

    def loss(key: str) -> str:
        return run(app_client, world, key).json()["result"]["totals"]["gross_loss"]

    shocks = {
        "G1": [{"kind": "default", "leis": [CORP], "loss_given_default_percent": "100"}],
        "G2": [
            {
                "kind": "default",
                "leis": [CORP],
                "loss_given_default_percent": "100",
                "include_group": False,
            }
        ],
        "F1": [{"kind": "fx", "currency": "USD", "change_percent": "-50"}],
        "H1": [{"kind": "haircut", "percent": "10", "counterparty_jurisdiction": "DE"}],
        "H2": [{"kind": "haircut", "percent": "10", "exposure_type": "bond"}],
    }
    for key, s in shocks.items():
        assert (
            put_scenario(app_client, world, scenario(key, *s, insurance=False)).status_code == 200
        )
    assert loss("G1") == "140.00"  # CORP 100 + its controlled subsidiary CORP2 40
    assert loss("G2") == "100.00"
    assert loss("F1") == "20.00"  # the USD position (40.00 EUR) loses half
    assert loss("H1") == "22.00"  # every position: 220 EUR x 10 %
    assert loss("H2") == "5.00"  # only the PLN bond (50.00 EUR)


def test_same_inputs_same_outputs(app_client: TestClient, world: dict[str, str]) -> None:
    a, b = run(app_client, world).json(), run(app_client, world).json()
    assert a["run"]["run_id"] != b["run"]["run_id"]
    assert a["run"]["input_hash"] == b["run"]["input_hash"]
    assert a["run"]["result_hash"] == b["run"]["result_hash"] and a["result"] == b["result"]
    assert a["run"]["signature"] != b["run"]["signature"]  # bound to the run id


def test_old_runs_are_frozen(app_client: TestClient, world: dict[str, str]) -> None:
    first = run(app_client, world).json()
    app_client.post(
        "/v1/exposures/batches",
        headers=world,
        json=batch(pos("L9", BANK, CORP, "900", as_of="2026-03-02")),
    )
    assert (
        put_contract(app_client, world, contract("P-1", INS, BANK, "5"), "cut cover").status_code
        == 200
    )
    later = run(app_client, world).json()
    assert (
        later["run"]["result_hash"] != first["run"]["result_hash"]
        and later["run"]["max_batch"] == 2
    )
    again = app_client.get(f"/v1/stress/runs/{first['run']['run_id']}", headers=world).json()
    assert again == first  # exactly what was stored
    assert app_client.post(
        f"/v1/stress/runs/{first['run']['run_id']}/verify", headers=world
    ).json()["ok"]
    pinned = run(app_client, world, max_batch=1).json()  # rebuilds the first run's data view
    assert pinned["run"]["max_batch"] == 1


def test_capital_buffers(app_client: TestClient, world: dict[str, str]) -> None:
    put_scenario(app_client, world, scenario("NI", insurance=False))
    body = run(app_client, world, "NI", capital_buffers={BANK: "49.99", BANK2: "10"}).json()
    by = {h["holder_lei"]: h for h in body["result"]["holders"]}
    assert by[BANK]["breach"] is True and by[BANK]["capital_buffer"] == "49.99"
    assert by[BANK2]["breach"] is False and body["result"]["totals"]["breaches"] == 1


def test_snapshot_lineage_and_listing(app_client: TestClient, world: dict[str, str]) -> None:
    r = run(app_client, world).json()["run"]
    snap = app_client.get(f"/v1/stress/runs/{r['run_id']}/snapshot", headers=world).json()
    assert snap["snapshot"]["lineage"]["batch_ids"] == [1]
    assert [f["currency"] for f in snap["snapshot"]["lineage"]["fx"]] == ["EUR", "PLN", "USD"]
    assert [c["contract_ref"] for c in snap["snapshot"]["lineage"]["contracts"]] == ["P-1"]
    run(app_client, world)
    listed = app_client.get(
        "/v1/stress/runs?scenario_key=SC-1&limit=1&after=1", headers=world
    ).json()
    assert [x["run_id"] for x in listed] == [2]


def test_run_requires_known_scenario_and_registered_leis(
    app_client: TestClient, world: dict[str, str]
) -> None:
    assert run(app_client, world, "NOPE").status_code == 404
    from tests.conftest import make_lei

    bad = scenario(
        "BAD", {"kind": "default", "leis": [make_lei("GHOST")], "loss_given_default_percent": "10"}
    )
    put_scenario(app_client, world, bad)
    assert run(app_client, world, "BAD").status_code == 422
    assert run(app_client, world, currency="SEK").status_code == 422  # no rate: nothing guessed
    assert run(app_client, world, currency="XXX").status_code == 422


def test_tampering_is_detected(
    app_client: TestClient, services: Services, world: dict[str, str]
) -> None:
    ids = [run(app_client, world).json()["run"]["run_id"] for _ in range(4)]
    drop_trigger(
        services.db, "run_immutable", "stress_runs"
    )  # what an insider with DB access would do
    services.db.execute(
        "UPDATE stress_runs SET result_json = replace(result_json, '50.00', '1.00') WHERE run_id = ?",
        (ids[0],),
    )
    services.db.execute(
        "UPDATE stress_runs SET signature = 'k1.deadbeef' WHERE run_id = ?", (ids[1],)
    )
    services.db.execute(
        "UPDATE stress_runs SET snapshot_json = replace(snapshot_json, '100.00', '1.00') WHERE run_id = ?",
        (ids[2],),
    )
    services.db.execute(
        "UPDATE stress_runs SET scenario_json = replace(scenario_json, '\"50', '\"10') WHERE run_id = ?",
        (ids[3],),
    )
    checks = [app_client.post(f"/v1/stress/runs/{i}/verify", headers=world).json() for i in ids]
    assert not any(c["ok"] for c in checks)
    assert checks[0]["result_hash_ok"] is False and checks[0]["result_reproduced"] is False
    assert checks[1]["signature_ok"] is False and checks[1]["result_reproduced"] is True
    assert checks[2]["input_hash_ok"] is False
    assert checks[3]["input_hash_ok"] is False or checks[3]["scenario_matches_version"] is False


def test_audit_trail(app_client: TestClient, auth: Auth, world: dict[str, str]) -> None:
    run(app_client, world)
    admin = auth("root", Role.TENANT_ADMIN)
    assert app_client.get("/v1/audit/verify", headers=admin).json()["ok"] is True
    actions = {e["action"] for e in app_client.get("/v1/audit", headers=admin).json()}
    assert {"scenario.created", "stress.run"} <= actions
