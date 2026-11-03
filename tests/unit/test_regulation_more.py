"""Extra branches: reject, API decisions, malformed tokens, locale edge cases."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from reggraph.api.deps import Services
from reggraph.core.errors import AuthenticationError, ConflictError, NotFoundError
from reggraph.security.rbac import Principal, Role
from tests.conftest import rule_payload

Auth = Callable[..., dict[str, str]]


def test_reject_then_propose_again(app_client: TestClient, auth: Auth) -> None:
    ana, omar = auth("ana", Role.ANALYST), auth("omar", Role.COMPLIANCE_OFFICER)
    app_client.post("/v1/rules", headers=ana, json={"rule": rule_payload(), "change_summary": "a"})
    rej = app_client.post(
        "/v1/rules/EU.CRR3.CET1/versions/1/reject", headers=omar, json={"note": "wrong article"}
    )
    assert rej.status_code == 200 and rej.json()["status"] == "rejected"
    again = app_client.post(
        "/v1/rules", headers=ana, json={"rule": rule_payload(), "change_summary": "b"}
    )
    assert again.status_code == 201 and again.json()["version_no"] == 2


def test_cannot_decide_twice_or_on_missing_version(app_client: TestClient, auth: Auth) -> None:
    ana, omar = auth("ana", Role.ANALYST), auth("omar", Role.COMPLIANCE_OFFICER)
    app_client.post("/v1/rules", headers=ana, json={"rule": rule_payload(), "change_summary": "a"})
    url = "/v1/rules/EU.CRR3.CET1/versions/1/approve"
    assert app_client.post(url, headers=omar, json={}).status_code == 200
    assert app_client.post(url, headers=omar, json={}).status_code == 409
    missing = "/v1/rules/EU.CRR3.CET1/versions/9/approve"
    assert app_client.post(missing, headers=omar, json={}).status_code == 404


def test_withdraw_and_ingest_over_http(app_client: TestClient, auth: Auth) -> None:
    ana, omar = auth("ana", Role.ANALYST), auth("omar", Role.COMPLIANCE_OFFICER)
    bot = auth("feed", Role.INGESTION_SERVICE)
    ing = app_client.post(
        "/v1/ingest", headers=bot, json={"rule": rule_payload(), "source_ref": "f"}
    )
    assert ing.status_code == 201 and ing.json()["task"]["status"] == "open"
    tasks = app_client.get("/v1/review-tasks", headers=ana).json()
    assert len(tasks) == 1
    app_client.post("/v1/rules/EU.CRR3.CET1/versions/1/approve", headers=omar, json={})
    assert app_client.get("/v1/review-tasks", headers=ana).json() == []
    wd = app_client.post("/v1/rules/EU.CRR3.CET1/withdraw", headers=omar, json={"note": "repealed"})
    assert wd.status_code == 200 and wd.json()["status"] == "withdrawn"
    assert app_client.get("/v1/rules", headers=ana).json() == []


def test_applicable_rejects_unknown_jurisdiction(app_client: TestClient, auth: Auth) -> None:
    r = app_client.get(
        "/v1/rules/applicable",
        params={"jurisdiction": "ZZ", "entity_type": "bank", "basis": "both"},
        headers=auth("ana", Role.ANALYST),
    )
    assert r.status_code == 422


@pytest.mark.parametrize(
    "token", ["", "x", "v1", "v1.k1", "v1.k1.a", "v2.k1.e30.AAAA", "v1.k1.!!!.AAAA", "a.b.c.d.e"]
)
def test_malformed_tokens(services: Services, token: str) -> None:
    with pytest.raises(AuthenticationError):
        services.signer.verify(token)


def test_token_with_unknown_role_is_refused(services: Services) -> None:
    good = services.signer.issue(Principal("ana", "acme", frozenset({Role.ANALYST})))
    version, kid, payload, _ = good.split(".")
    forged = ".".join([version, kid, payload, "A" * 43])
    with pytest.raises(AuthenticationError):
        services.signer.verify(forged)


def test_domain_errors_carry_status_and_key() -> None:
    assert NotFoundError("x").http_status == 404
    assert ConflictError("x").http_status == 409
