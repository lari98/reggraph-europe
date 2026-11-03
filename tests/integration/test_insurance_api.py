"""Insurance contracts and the combined connectivity views through the HTTP stack."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from reggraph.security.rbac import Role
from tests.phase3 import (
    BANK,
    BANK2,
    CORP,
    INS,
    INS2,
    REINS,
    Auth,
    batch,
    contract,
    pos,
    put_contract,
    register_all,
    set_rates,
)


@pytest.fixture
def world(app_client: TestClient, auth: Auth) -> dict[str, str]:
    h = auth("ana", Role.ANALYST)
    register_all(app_client, h)
    set_rates(app_client, auth)
    assert put_contract(app_client, h, contract("P-1", INS, BANK, "1000")).status_code == 200
    assert (
        put_contract(app_client, h, contract("P-2", INS2, BANK, "500", currency="USD")).status_code
        == 200
    )
    assert (
        put_contract(
            app_client, h, contract("P-3", INS, BANK, "300", end_date="2026-02-01")
        ).status_code
        == 200
    )
    reins = contract("RE-1", REINS, INS, "800", line="reinsurance", reinsures_ref="P-1")
    assert put_contract(app_client, h, reins).status_code == 200
    loaded = app_client.post(
        "/v1/exposures/batches",
        headers=h,
        json=batch(
            pos("L1", BANK, INS, "100"), pos("L2", BANK, CORP, "300"), pos("L3", BANK2, BANK, "50")
        ),
    )
    assert loaded.status_code == 201, loaded.text
    return h


def test_contract_versions_and_history(app_client: TestClient, world: dict[str, str]) -> None:
    changed = put_contract(app_client, world, contract("P-1", INS, BANK, "1200"), "raised cover")
    assert changed.json()["version_no"] == 2
    assert (
        put_contract(app_client, world, contract("P-1", INS, BANK, "1200")).status_code == 409
    )  # no empty change
    history = app_client.get("/v1/contracts/P-1/history", headers=world).json()
    assert [v["version_no"] for v in history] == [1, 2]
    assert history[0]["contract"]["sum_insured"] == "1000.00"
    assert (
        app_client.get("/v1/contracts/P-1", headers=world).json()["contract"]["sum_insured"]
        == "1200.00"
    )
    assert app_client.get("/v1/contracts/NOPE", headers=world).status_code == 404


def test_path_and_body_must_agree(app_client: TestClient, world: dict[str, str]) -> None:
    body = {"contract": contract("P-9", INS, BANK, "1"), "change_summary": "x"}
    assert app_client.put("/v1/contracts/P-1", headers=world, json=body).status_code == 422


def test_insurer_must_be_an_insurer_and_parties_registered(
    app_client: TestClient, world: dict[str, str]
) -> None:
    assert (
        put_contract(app_client, world, contract("X-1", CORP, BANK, "1")).status_code == 422
    )  # a corporate
    assert (
        put_contract(
            app_client, world, contract("X-2", INS, BANK, "1", reinsures_ref="P-1")
        ).status_code
        == 422
    )
    unknown = contract("X-3", INS, BANK, "1", line="reinsurance", reinsures_ref="NO-SUCH")
    assert put_contract(app_client, world, unknown).status_code == 422
    from tests.conftest import make_lei

    assert (
        put_contract(app_client, world, contract("X-4", INS, make_lei("GHOST"), "1")).status_code
        == 422
    )


def test_listing_filters(app_client: TestClient, world: dict[str, str]) -> None:
    def refs(query: str) -> list[str]:
        found = app_client.get(f"/v1/contracts{query}", headers=world).json()
        return [c["contract_ref"] for c in found]

    assert refs("") == ["P-1", "P-2", "P-3", "RE-1"]
    assert refs(f"?insurer={INS2}") == ["P-2"]
    assert refs(f"?insured={INS}") == ["RE-1"]
    assert refs("?line=reinsurance") == ["RE-1"]
    assert refs("?limit=2&after=P-1") == ["P-2", "P-3"]
    assert app_client.get("/v1/contracts?line=pirate", headers=world).status_code == 422


def test_insurance_profile(app_client: TestClient, auth: Auth, world: dict[str, str]) -> None:
    body = app_client.get(
        f"/v1/entities/{BANK}/insurance?on=2026-03-02&currency=EUR", headers=world
    ).json()
    # P-1 1000 EUR + P-2 500 USD (=400 EUR); P-3 ended on 2026-02-01 and does not count.
    assert body["total_cover"] == "1400.00"
    assert [(s["key"], s["total"]) for s in body["shares"]] == [(INS, "1000.00"), (INS2, "400.00")]
    assert [c["contract_ref"] for c in body["contracts"]] == ["P-1", "P-2"]
    assert body["contracts"][0]["reinsured_by"] == [REINS]
    assert {c["contract_ref"] for c in body["contract_lineage"]} == {"P-1", "P-2"}
    set_rates(app_client, auth, "2026-01-30")
    last = app_client.get(f"/v1/entities/{BANK}/insurance?on=2026-02-01", headers=world).json()
    assert [c["contract_ref"] for c in last["contracts"]] == ["P-1", "P-2", "P-3"]  # end day counts
    gone = app_client.get(f"/v1/entities/{BANK}/insurance?on=2026-02-02", headers=world).json()
    assert [c["contract_ref"] for c in gone["contracts"]] == ["P-1", "P-2"]


def test_interconnection_finds_double_exposure(
    app_client: TestClient, world: dict[str, str]
) -> None:
    body = app_client.get(
        f"/v1/entities/{BANK}/interconnection?on=2026-03-02&currency=EUR", headers=world
    ).json()
    # INS is a counterparty of BANK (loan 100) and also its insurer (cover 1000).
    assert body["items"] == [{"lei": INS, "exposure_total": "100.00", "cover_total": "1000.00"}]
    assert len(body["result_hash"]) == 64 and body["lineage"]["batch_ids"] == [1]


def test_ecosystem_graph_has_three_kinds_of_edge(
    app_client: TestClient, world: dict[str, str]
) -> None:
    link = {"parent_lei": INS, "child_lei": INS2, "percent": "55", "effective_from": "2020-01-01"}
    assert app_client.post("/v1/ownership", headers=world, json=link).status_code == 201
    one = app_client.get(
        f"/v1/entities/{BANK}/ecosystem?on=2026-03-02&depth=1", headers=world
    ).json()
    assert sorted({e["kind"] for e in one["edges"]}) == ["exposure", "insurance", "ownership"]
    assert set(one["nodes"]) == {BANK, BANK2, CORP, INS, INS2} and REINS not in one["nodes"]
    assert [
        e["kind"]
        for e in app_client.get(
            f"/v1/entities/{BANK}/ecosystem?on=2026-03-02&depth=0", headers=world
        ).json()["edges"]
    ] == []
    two = app_client.get(
        f"/v1/entities/{BANK}/ecosystem?on=2026-03-02&depth=2", headers=world
    ).json()
    assert REINS in two["nodes"]
    own = next(e for e in two["edges"] if e["kind"] == "ownership")
    assert (own["from_lei"], own["to_lei"], own["percent"]) == (INS, INS2, "55")
    again = app_client.get(
        f"/v1/entities/{BANK}/ecosystem?on=2026-03-02&depth=2", headers=world
    ).json()
    assert again == two  # reproducible, hash included


def test_ecosystem_reproducible_after_new_data(
    app_client: TestClient, world: dict[str, str]
) -> None:
    url = f"/v1/entities/{BANK}/ecosystem?on=2026-03-02&depth=2&max_batch=1"
    before = app_client.get(url, headers=world).json()
    app_client.post(
        "/v1/exposures/batches",
        headers=world,
        json=batch(pos("L9", BANK, CORP, "5", as_of="2026-03-02")),
    )
    assert app_client.get(url, headers=world).json() == before


def test_depth_limit_enforced(app_client: TestClient, world: dict[str, str]) -> None:
    assert (
        app_client.get(f"/v1/entities/{BANK}/ecosystem?depth=5", headers=world).status_code == 422
    )
    assert (
        app_client.get(f"/v1/entities/{BANK}/ecosystem?depth=-1", headers=world).status_code == 422
    )


def test_contract_changes_are_audited(
    app_client: TestClient, auth: Auth, world: dict[str, str]
) -> None:
    admin = auth("root", Role.TENANT_ADMIN)
    assert app_client.get("/v1/audit/verify", headers=admin).json()["ok"] is True
    actions = {e["action"] for e in app_client.get("/v1/audit", headers=admin).json()}
    assert {"contract.created", "exposure.batch_loaded"} <= actions
