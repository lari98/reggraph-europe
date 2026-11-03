"""End-to-end API behaviour through the real ASGI stack."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from reggraph.security.rbac import Role
from tests.conftest import rule_payload

Auth = Callable[..., dict[str, str]]


def test_public_reference_data(app_client: TestClient) -> None:
    assert app_client.get("/health").json()["status"] == "ok"
    tags = {x["tag"] for x in app_client.get("/v1/locales").json()}
    assert {"de", "fr", "pl", "en-GB", "en-US", "zh-SG", "ms-SG", "ta-SG"} <= tags
    jur = {x["code"]: x for x in app_client.get("/v1/jurisdictions").json()}
    assert len(jur) == 31 and jur["PL"]["currency"] == "PLN" and not jur["PL"]["euro_area"]
    assert {c["code"] for c in app_client.get("/v1/currencies").json()} >= {"EUR", "SEK", "SGD"}


def test_rule_lifecycle(app_client: TestClient, auth: Auth) -> None:
    ana, omar = auth("ana", Role.ANALYST), auth("omar", Role.COMPLIANCE_OFFICER)
    r = app_client.post(
        "/v1/rules", headers=ana, json={"rule": rule_payload(), "change_summary": "initial"}
    )
    assert r.status_code == 201 and r.json()["status"] == "draft"
    assert app_client.get("/v1/rules", headers=ana).json() == []
    ok = app_client.post("/v1/rules/EU.CRR3.CET1/versions/1/approve", headers=omar, json={})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"
    assert len(app_client.get("/v1/rules", headers=ana).json()) == 1
    hist = app_client.get("/v1/rules/EU.CRR3.CET1", headers=ana).json()
    assert [v["version_no"] for v in hist] == [1]
    found = app_client.get(
        "/v1/rules/applicable",
        params={
            "jurisdiction": "DE",
            "entity_type": "bank",
            "basis": "consolidated",
            "on": "2025-06-01",
        },
        headers=ana,
    ).json()
    assert len(found) == 1


def test_admin_audit_chain_via_api(app_client: TestClient, auth: Auth) -> None:
    ana, admin = auth("ana", Role.ANALYST), auth("root", Role.TENANT_ADMIN)
    app_client.post(
        "/v1/rules", headers=ana, json={"rule": rule_payload(), "change_summary": "initial"}
    )
    verify = app_client.get("/v1/audit/verify", headers=admin).json()
    assert verify["ok"] and verify["entries"] >= 1
    assert app_client.get("/v1/audit", headers=admin).json()[0]["action"]


def test_fx_write_and_convert_non_euro_eu_currency(app_client: TestClient, auth: Auth) -> None:
    admin, viewer = auth("root", Role.TENANT_ADMIN), auth("vi", Role.VIEWER)
    put = app_client.put(
        "/v1/fx-rates",
        headers=admin,
        json={
            "as_of": "2026-03-02",
            "source": "ECB reference",
            "rates": {"USD": "1.10", "CZK": "25.00", "PLN": "4.30"},
        },
    )
    assert put.status_code == 200 and put.json() == {"stored": 3}
    out = app_client.post(
        "/v1/convert", headers=viewer, json={"amount": "1000", "from": "EUR", "to": "CZK"}
    ).json()
    assert out["result"] == {"amount": "25000.00", "currency": "CZK"}


def test_errors_are_localized(app_client: TestClient) -> None:
    de = app_client.get("/v1/rules", headers={"Accept-Language": "de"})
    pl = app_client.get("/v1/rules", headers={"Accept-Language": "pl"})
    assert de.status_code == pl.status_code == 401
    assert de.json()["message"] != pl.json()["message"]
    assert de.headers["content-language"] == "de"
    assert de.headers["www-authenticate"] == "Bearer"


def test_docs_hidden_in_prod(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from reggraph.api import create_app
    from reggraph.config import Settings

    cfg = Settings(
        env="prod",
        db_path=str(tmp_path / "r.db"),
        token_keys={"k1": b"Zk3p9Qw7Lm2Xv8Bn4Tr6Yh1Uc5Ja0Sd7Fe"},
        active_kid="k1",
    )
    client = TestClient(create_app(cfg))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
