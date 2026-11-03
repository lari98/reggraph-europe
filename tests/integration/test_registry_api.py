"""Registry through the real HTTP stack."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from reggraph.security.rbac import Role
from tests.conftest import entity_payload, make_lei, rule_payload

Auth = Callable[..., dict[str, str]]
A, B, C = make_lei("AAAA"), make_lei("BBBB"), make_lei("CCCC")


def _put(client: TestClient, h: dict[str, str], lei: str, **over: object):  # type: ignore[no-untyped-def]
    return client.put(
        f"/v1/entities/{lei}",
        headers=h,
        json={"entity": entity_payload(lei, **over), "change_summary": "entry"},
    )


def _own(client: TestClient, h: dict[str, str], parent: str, child: str, pct: str = "60"):  # type: ignore[no-untyped-def]
    return client.post(
        "/v1/ownership",
        headers=h,
        json={
            "parent_lei": parent,
            "child_lei": child,
            "percent": pct,
            "effective_from": "2024-01-01",
        },
    )


def test_entity_lifecycle(app_client: TestClient, auth: Auth) -> None:
    ana = auth("ana", Role.ANALYST)
    first = _put(app_client, ana, A, jurisdiction="PL", reporting_basis="both")
    assert first.status_code == 200 and first.json()["version_no"] == 1
    assert _put(app_client, ana, A, jurisdiction="PL", reporting_basis="both").status_code == 409
    second = _put(app_client, ana, A, jurisdiction="PL", legal_name="Polish Bank SA")
    assert second.json()["version_no"] == 2
    got = app_client.get(f"/v1/entities/{A.lower()}", headers=ana).json()  # lower-case accepted
    assert got["entity"]["legal_name"] == "Polish Bank SA" and got["entity"]["jurisdiction"] == "PL"
    hist = app_client.get(f"/v1/entities/{A}/history", headers=ana).json()
    assert [v["version_no"] for v in hist] == [1, 2]
    listed = app_client.get("/v1/entities", params={"jurisdiction": "PL"}, headers=ana).json()
    assert [e["lei"] for e in listed] == [A]
    assert app_client.get("/v1/entities", params={"jurisdiction": "DE"}, headers=ana).json() == []


def test_group_flow_with_dates(app_client: TestClient, auth: Auth) -> None:
    ana = auth("ana", Role.ANALYST)
    for lei in (A, B, C):
        _put(app_client, ana, lei, legal_name=f"Entity {lei[:4]}")
    first = _own(app_client, ana, A, B, "60")
    assert first.status_code == 201 and first.json()["link_id"] == 1
    assert _own(app_client, ana, B, C, "80").status_code == 201
    group = app_client.get(f"/v1/entities/{A}/group", params={"on": "2025-06-01"}, headers=ana)
    body = group.json()
    assert {m["lei"]: m["effective_percent"] for m in body["members"]} == {
        B: "60.0000",
        C: "48.0000",
    }
    assert "indicative" in body["note"]
    up = app_client.get(
        f"/v1/entities/{C}/ultimate-parent", params={"on": "2025-06-01"}, headers=ana
    )
    assert up.json()["lei"] == A
    own = app_client.get(
        f"/v1/entities/{B}/ownership", params={"on": "2025-06-01"}, headers=ana
    ).json()
    assert [x["link"]["parent_lei"] for x in own["parents"]] == [A]
    assert [x["link"]["child_lei"] for x in own["children"]] == [C]
    ended = app_client.post(
        "/v1/ownership/1/end", headers=ana, json={"effective_until": "2025-12-31"}
    )
    assert ended.status_code == 200 and ended.json()["link"]["effective_until"] == "2025-12-31"
    later = app_client.get(f"/v1/entities/{A}/group", params={"on": "2026-02-01"}, headers=ana)
    assert later.json()["members"] == []


def test_rules_for_entity_over_http(app_client: TestClient, auth: Auth) -> None:
    ana, omar = auth("ana", Role.ANALYST), auth("omar", Role.COMPLIANCE_OFFICER)
    app_client.post(
        "/v1/rules",
        headers=ana,
        json={"rule": rule_payload(consolidation="individual"), "change_summary": "x"},
    )
    app_client.post("/v1/rules/EU.CRR3.CET1/versions/1/approve", headers=omar, json={})
    _put(app_client, ana, A, jurisdiction="DE")
    res = app_client.get(f"/v1/entities/{A}/rules", params={"on": "2025-06-01"}, headers=ana).json()
    assert len(res) == 1 and res[0]["because_of"] == {
        "jurisdictions": ["DE"],
        "bases": ["individual"],
    }


def test_errors_are_localized_and_typed(app_client: TestClient, auth: Auth) -> None:
    ana = auth("ana", Role.ANALYST)
    nf = app_client.get(f"/v1/entities/{A}", headers={**ana, "Accept-Language": "fr"})
    assert nf.status_code == 404 and nf.json()["code"] == "not_found"
    bad = app_client.get("/v1/entities/not-a-lei", headers=ana)
    assert bad.status_code == 422
    assert (
        app_client.get("/v1/entities", params={"jurisdiction": "ZZ"}, headers=ana).status_code
        == 422
    )
