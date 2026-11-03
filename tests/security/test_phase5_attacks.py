"""Hostile-input and abuse tests for Phase 5: SSO, connectors, outbound access, log secrets."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reggraph.config import Settings
from reggraph.core.clock import FixedClock
from reggraph.core.errors import ValidationFailure
from reggraph.security.rbac import Principal, Role
from tests.conftest import KEY
from tests.phase5 import (
    AUDIENCE,
    ECB_URL,
    FEED_URL,
    ISSUER,
    JWKS_URL,
    FakeFetcher,
    Idp,
    celex_link,
    client_with,
    ecb_xml,
    rss,
)

SSO: dict[str, Any] = {
    "oidc_issuer": ISSUER,
    "oidc_audience": AUDIENCE,
    "oidc_jwks_url": JWKS_URL,
}


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


@pytest.fixture
def idp() -> Idp:
    return Idp()


@pytest.fixture
def fetcher() -> FakeFetcher:
    return FakeFetcher({ECB_URL: ecb_xml(), FEED_URL: rss(("Act", celex_link("32026R0100"), ""))})


@pytest.fixture
def sso_client(clock: FixedClock, idp: Idp, fetcher: FakeFetcher) -> Iterator[TestClient]:
    with client_with(
        clock,
        connector_fetcher=fetcher,
        oidc_fetcher=FakeFetcher({JWKS_URL: idp.jwks()}),
        **SSO,
    ) as c:
        yield c


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def local(client: TestClient, role: Role, tenant: str = "acme") -> dict[str, str]:
    signer = client.app.state.services.signer  # type: ignore[attr-defined]
    return bearer(signer.issue(Principal("u1", tenant, frozenset({role}))))


# ------------------------------------------------------------------ SSO through the API
def test_sso_token_opens_the_api_with_its_own_roles(
    sso_client: TestClient, idp: Idp, clock: FixedClock
) -> None:
    analyst = bearer(idp.token(clock))
    assert sso_client.get("/v1/dashboard", headers=analyst).status_code == 200
    assert sso_client.post("/v1/connectors/ecb_rates/run", headers=analyst).status_code == 403
    admin = bearer(idp.token(clock, reggraph_roles=["tenant_admin"]))
    assert sso_client.post("/v1/connectors/ecb_rates/run", headers=admin).status_code == 200


def test_tenant_comes_only_from_the_verified_claim(
    sso_client: TestClient, idp: Idp, clock: FixedClock
) -> None:
    admin_acme = bearer(idp.token(clock, reggraph_roles=["tenant_admin"]))
    sso_client.post("/v1/connectors/eurlex_watch/run", headers=admin_acme)
    spoof = {
        **bearer(idp.token(clock, reggraph_tenant="globex")),
        "X-Tenant-Id": "acme",
        "Tenant": "acme",
    }
    assert sso_client.get("/v1/source-alerts?tenant_id=acme", headers=spoof).json() == []


def test_forged_and_replayed_sso_tokens(
    sso_client: TestClient, idp: Idp, clock: FixedClock
) -> None:
    good = idp.token(clock)
    assert sso_client.get("/v1/dashboard", headers=bearer(good)).status_code == 200
    for bad in (good[:-3] + "AAA", good + "x", "Bearer " + good, good.replace(".", "..", 1), ""):
        assert sso_client.get("/v1/dashboard", headers=bearer(bad)).status_code == 401
    clock.advance(minutes=11)
    assert sso_client.get("/v1/dashboard", headers=bearer(good)).status_code == 401  # expired
    assert (
        sso_client.get(f"/v1/dashboard?access_token={good}").status_code == 401
    )  # not from the URL


def test_without_sso_a_jwt_is_just_garbage(
    clock: FixedClock, idp: Idp, fetcher: FakeFetcher
) -> None:
    with client_with(clock, connector_fetcher=fetcher) as c:
        assert c.get("/v1/dashboard", headers=bearer(idp.token(clock))).status_code == 401


def test_local_tokens_can_be_switched_off(
    clock: FixedClock, idp: Idp, fetcher: FakeFetcher
) -> None:
    with client_with(
        clock,
        connector_fetcher=fetcher,
        oidc_fetcher=FakeFetcher({JWKS_URL: idp.jwks()}),
        allow_local_tokens=False,
        **SSO,
    ) as c:
        assert c.get("/v1/dashboard", headers=local(c, Role.ANALYST)).status_code == 401
        assert c.get("/v1/dashboard", headers=bearer(idp.token(clock))).status_code == 200


def test_local_token_with_sso_on_still_works_and_looks_like_before(
    sso_client: TestClient,
) -> None:
    assert (
        sso_client.get("/v1/dashboard", headers=local(sso_client, Role.VIEWER)).status_code == 200
    )
    forged = local(sso_client, Role.VIEWER)["Authorization"][:-4] + "AAAA"
    assert sso_client.get("/v1/dashboard", headers={"Authorization": forged}).status_code == 401


def test_a_sso_token_cannot_pose_as_a_local_one(
    sso_client: TestClient, idp: Idp, clock: FixedClock
) -> None:
    token = idp.token(clock)
    fake = "v1.k1." + token.split(".")[1] + "." + token.split(".")[2]
    assert sso_client.get("/v1/dashboard", headers=bearer(fake)).status_code == 401


# ------------------------------------------------------------------ settings are validated
@pytest.mark.parametrize(
    "over",
    [
        {"oidc_issuer": ISSUER},  # partial
        {"oidc_issuer": ISSUER, "oidc_audience": AUDIENCE},
        {**SSO, "oidc_issuer": "http://login.example-idp.test"},
        {**SSO, "oidc_jwks_url": "http://login.example-idp.test/keys"},
        {**SSO, "oidc_jwks_url": "https://10.0.0.1/keys"},  # IP literal
        {**SSO, "oidc_jwks_url": "https://localhost/keys"},
        {**SSO, "oidc_jwks_url": "https://user:pw@login.example-idp.test/keys"},
        {**SSO, "oidc_jwks_url": "https://login.example-idp.test:8443/keys"},
        {**SSO, "oidc_tenant_claim": "a b"},
        {**SSO, "oidc_roles_claim": "x" * 70},
        {"allow_local_tokens": False},  # nobody could log in
        {"connector_hosts": ("*.europa.eu",)},
        {"connector_hosts": ("localhost",)},
        {"connector_hosts": ("127.0.0.1",)},
        {"connector_hosts": ("WWW.ECB.EUROPA.EU",)},
        {"ecb_rates_url": "http://www.ecb.europa.eu/x.xml"},
        {"ecb_rates_url": "https://evil.example/x.xml"},
        {"eurlex_feed_url": "https://169.254.169.254/latest/meta-data"},
    ],
)
def test_unsafe_settings_do_not_start(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationFailure):
        Settings(env="test", token_keys={"k1": KEY}, active_kid="k1", **over)


def test_settings_never_print_secrets() -> None:
    s = Settings(
        env="test",
        token_keys={"k1": KEY},
        active_kid="k1",
        database_url="postgresql://u:hunter2hunter2@h/db",
    )
    assert "hunter2" not in repr(s) and KEY.decode() not in repr(s) and "hunter2" not in str(s)


# ------------------------------------------------------------------ outbound abuse
def test_callers_cannot_steer_the_outbound_request(clock: FixedClock, fetcher: FakeFetcher) -> None:
    with client_with(clock, connector_fetcher=fetcher) as c:
        admin = local(c, Role.TENANT_ADMIN)
        evil = "http://169.254.169.254/latest/meta-data"
        r = c.post(
            f"/v1/connectors/ecb_rates/run?url={evil}&host=evil.example",
            headers={**admin, "Host": "evil.example", "X-Forwarded-Host": "evil.example"},
            json={"url": evil},
        )
        assert r.status_code == 200
        assert fetcher.calls == [ECB_URL]


def test_hostile_feed_cannot_plant_links_or_markup(clock: FixedClock) -> None:
    hostile = FakeFetcher(
        {
            FEED_URL: rss(
                ("&lt;img src=x onerror=alert(1)&gt;", celex_link("32026R0100"), ""),
                ("Phish", "https://eur-lex.europa.eu.evil.example/login", ""),
                ("Script", "javascript:alert(1)", ""),
                ("Other", "https://evil.example/CELEX:32026R0200", ""),
            )
        }
    )
    with client_with(clock, connector_fetcher=hostile) as c:
        admin = local(c, Role.TENANT_ADMIN)
        out = c.post("/v1/connectors/eurlex_watch/run", headers=admin).json()
        assert out["summary"] == {"items_seen": 1, "new_alerts": 1, "skipped": 3}
        resp = c.get("/v1/source-alerts", headers=admin)
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.headers["x-content-type-options"] == "nosniff"
        alerts = resp.json()
        assert [a["url"] for a in alerts] == [celex_link("32026R0100")]
        assert alerts[0]["title"] == "<img src=x onerror=alert(1)>"  # inert text, not markup


def test_poisoned_rate_file_stores_nothing(clock: FixedClock) -> None:
    poisoned = FakeFetcher({ECB_URL: ecb_xml(rates={"USD": "1.25", "PLN": "99999999"})})
    with client_with(clock, connector_fetcher=poisoned) as c:
        admin = local(c, Role.TENANT_ADMIN)
        run = c.post("/v1/connectors/ecb_rates/run", headers=admin).json()
        assert run["status"] == "failed"
        rows = c.app.state.services.db.query("SELECT COUNT(*) AS n FROM fx_rates")  # type: ignore[attr-defined]
        assert rows[0]["n"] == 0  # all-or-nothing: the good USD rate was not kept either


def test_oversized_and_slow_sources_fail_cleanly(clock: FixedClock) -> None:
    from reggraph.netio import UpstreamError

    broken = FakeFetcher(
        {ECB_URL: UpstreamError("response too large"), FEED_URL: UpstreamError("slow")}
    )
    with client_with(clock, connector_fetcher=broken) as c:
        admin = local(c, Role.TENANT_ADMIN)
        for name in ("ecb_rates", "eurlex_watch"):
            body = c.post(f"/v1/connectors/{name}/run", headers=admin).json()
            assert body["status"] == "failed" and body["summary"] == {"error": "upstream_error"}


def test_hammering_a_connector_reaches_the_source_once(
    clock: FixedClock, fetcher: FakeFetcher
) -> None:
    with client_with(clock, connector_fetcher=fetcher) as c:
        admin = local(c, Role.TENANT_ADMIN)
        codes = [
            c.post("/v1/connectors/ecb_rates/run", headers=admin).status_code for _ in range(30)
        ]
        assert codes[0] == 200 and set(codes[1:]) == {409}
        assert fetcher.calls.count(ECB_URL) == 1


def test_parallel_runs_store_the_rates_once(clock: FixedClock, fetcher: FakeFetcher) -> None:
    with client_with(clock, connector_fetcher=fetcher) as c:
        svc = c.app.state.services  # type: ignore[attr-defined]
        who = Principal("u1", "acme", frozenset({Role.TENANT_ADMIN}))
        outcomes: list[str] = []

        def go() -> None:
            try:
                outcomes.append(svc.connectors.run(who, "ecb_rates").status)
            except Exception as exc:
                outcomes.append(type(exc).__name__)

        threads = [threading.Thread(target=go) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert outcomes.count("succeeded") == 1
        assert set(outcomes) <= {"succeeded", "unchanged", "ConflictError"}
        rows = svc.db.query("SELECT COUNT(*) AS n FROM fx_rates")
        assert rows[0]["n"] == 3


# ------------------------------------------------------------------ nothing secret in logs
def test_no_secrets_reach_the_logs(
    caplog: pytest.LogCaptureFixture,
    clock: FixedClock,
    idp: Idp,
    fetcher: FakeFetcher,
) -> None:
    with (
        caplog.at_level(logging.DEBUG),
        client_with(
            clock,
            connector_fetcher=fetcher,
            oidc_fetcher=FakeFetcher({JWKS_URL: idp.jwks()}),
            **SSO,
        ) as c,
    ):
        svc = c.app.state.services  # type: ignore[attr-defined]
        local_token = svc.signer.issue(Principal("u1", "acme", frozenset({Role.TENANT_ADMIN})))
        sso_token = idp.token(clock, reggraph_roles=["tenant_admin"])
        broken = idp.token(clock)[:-4] + "AAAA"
        for token in (local_token, sso_token, broken, local_token[:-3] + "xyz"):
            c.get("/v1/dashboard", headers=bearer(token))
        c.post("/v1/connectors/ecb_rates/run", headers=bearer(sso_token))
        c.post("/v1/connectors/nope/run", headers=bearer(local_token))
        c.get("/v1/dashboard?currency=!!", headers=bearer(local_token))

        def boom(*_a: object, **_k: object) -> None:
            raise RuntimeError(
                f"explode while holding Bearer {sso_token} and password=hunter2hunter2"
            )

        svc.dashboard.build = boom  # force the unhandled-error path
        assert c.get("/v1/dashboard", headers=bearer(local_token)).status_code == 500
    text = caplog.text
    for secret in (
        local_token,
        sso_token,
        broken,
        sso_token.split(".")[2],  # the signature part alone
        local_token.split(".")[3],
        KEY.decode(),
        "hunter2hunter2",
    ):
        assert secret not in text, secret[:12]
    assert "unhandled error" in text  # the event itself is still logged


def test_error_bodies_never_echo_input(sso_client: TestClient, idp: Idp, clock: FixedClock) -> None:
    token = idp.token(clock)
    r = sso_client.get("/v1/dashboard?currency=" + "<script>", headers=bearer(token))
    assert r.status_code == 422 and "<script>" not in r.text and token not in r.text
    r = sso_client.get("/v1/dashboard", headers=bearer(token[:-2]))
    assert token[:30] not in r.text
