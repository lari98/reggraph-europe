"""Offline (train/plane) mode: nothing can leave the machine, SSO is off, serving is loopback."""

from __future__ import annotations

import socket
from datetime import UTC, datetime
from typing import Any

import pytest

from reggraph.cli import main
from reggraph.config import Settings
from reggraph.core.clock import FixedClock
from reggraph.core.errors import ValidationFailure
from reggraph.netio import OfflineFetcher, UpstreamError
from reggraph.security.rbac import Role
from tests.integration.test_connectors_api import hdr
from tests.phase5 import AUDIENCE, ISSUER, JWKS_URL, FakeFetcher, client_with

CLOCK = FixedClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


def test_offline_fetcher_always_refuses() -> None:
    with pytest.raises(UpstreamError, match="offline"):
        OfflineFetcher().get("https://www.ecb.europa.eu/x", ["application/xml"])


def test_connectors_are_switched_off_and_never_touch_the_network(monkeypatch: Any) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("a socket was opened in offline mode")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    fake = FakeFetcher()
    with client_with(CLOCK, connector_fetcher=fake, offline=True) as c:
        r = c.post("/v1/connectors/ecb_rates/run", headers=hdr(c, Role.TENANT_ADMIN))
        assert r.status_code == 422 and fake.calls == []
        body = c.get("/v1/dashboard", headers=hdr(c, Role.TENANT_ADMIN)).json()
        assert body["offline"] is True and body["labels"]["notice.decision_support"]


def test_online_mode_reports_offline_false() -> None:
    with client_with(CLOCK) as c:
        assert c.get("/v1/dashboard", headers=hdr(c, Role.VIEWER)).json()["offline"] is False


def test_offline_mode_rejects_sso_configuration() -> None:
    base: dict[str, Any] = {"env": "test", "token_keys": {"k": b"x" * 40}, "active_kid": "k"}
    with pytest.raises(ValidationFailure):
        Settings(
            **base, offline=True, oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_jwks_url=JWKS_URL
        )
    with pytest.raises(ValidationFailure):
        Settings(**base, offline=True, allow_local_tokens=False)


def test_offline_flag_comes_from_the_environment(monkeypatch: Any) -> None:
    monkeypatch.setenv("REGGRAPH_OFFLINE", "yes")
    monkeypatch.setenv("REGGRAPH_TOKEN_KEYS", "k:" + "a" * 40)
    monkeypatch.setenv("REGGRAPH_ACTIVE_KID", "k")
    assert Settings.from_env().offline is True


def test_serve_refuses_a_public_address_in_offline_mode(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("REGGRAPH_OFFLINE", "1")
    monkeypatch.setenv("REGGRAPH_ENV", "dev")
    monkeypatch.setenv("REGGRAPH_TOKEN_KEYS", "k:" + "a" * 40)
    monkeypatch.setenv("REGGRAPH_ACTIVE_KID", "k")
    assert main(["serve", "--host", "0.0.0.0"]) == 2  # noqa: S104 - proving it is refused
    assert "loopback" in capsys.readouterr().err


def test_demo_refuses_a_public_address(capsys: Any) -> None:
    assert main(["demo", "--host", "0.0.0.0"]) == 2  # noqa: S104
    assert "loopback" in capsys.readouterr().err


def test_demo_data_is_invented_and_complete() -> None:
    import secrets

    from reggraph.api.deps import build_services
    from reggraph.demo import seed

    s = Settings(
        env="dev",
        token_keys={"d": secrets.token_urlsafe(48).encode()},
        active_kid="d",
        offline=True,
    )
    svc = build_services(s, CLOCK)
    people = seed(svc)
    data = svc.dashboard.build(people["admin"], CLOCK.now().date(), "EUR")
    assert data["omitted"] == []
    assert data["sections"]["registry"]["entities"] == 15
    assert data["sections"]["stress"]["recent_runs"][0]["breaches"] >= 0
    assert any(r["breaches"] > 0 for r in data["sections"]["stress"]["recent_runs"])
    names = [e["name"] for e in data["sections"]["exposure"]["top_counterparties"]]
    assert all(n.endswith("(demo)") for n in names)
    viewer = svc.dashboard.build(people["viewer"], CLOCK.now().date(), "EUR")
    assert viewer["view"] != data["view"]
