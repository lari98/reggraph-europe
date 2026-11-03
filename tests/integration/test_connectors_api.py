"""Connectors through the HTTP stack: ECB rates, EUR-Lex watch, history, alerts, limits."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from reggraph.core.clock import FixedClock
from reggraph.netio import UpstreamError
from reggraph.security.rbac import Principal, Role
from tests.conftest import drop_trigger
from tests.phase5 import ECB_URL, FEED_URL, FakeFetcher, celex_link, client_with, ecb_xml, rss

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def fetcher() -> FakeFetcher:
    return FakeFetcher(
        {
            ECB_URL: ecb_xml("2026-03-02"),
            FEED_URL: rss(
                (
                    "Regulation (EU) 2026/100",
                    celex_link("32026R0100"),
                    "Mon, 02 Mar 2026 08:00:00 GMT",
                ),
                (
                    "Directive (EU) 2026/101",
                    celex_link("32026L0101"),
                    "Mon, 02 Mar 2026 08:30:00 GMT",
                ),
            ),
        }
    )


@pytest.fixture
def client(clock: FixedClock, fetcher: FakeFetcher) -> Iterator[TestClient]:
    with client_with(clock, connector_fetcher=fetcher) as c:
        yield c


def hdr(client: TestClient, role: Role, tenant: str = "acme", user: str = "u1") -> dict[str, str]:
    signer = client.app.state.services.signer  # type: ignore[attr-defined]
    return {"Authorization": f"Bearer {signer.issue(Principal(user, tenant, frozenset({role})))}"}


def run(client: TestClient, name: str, role: Role = Role.TENANT_ADMIN, tenant: str = "acme"):  # type: ignore[no-untyped-def]
    return client.post(f"/v1/connectors/{name}/run", headers=hdr(client, role, tenant))


def test_ecb_run_stores_supported_currencies_only(client: TestClient) -> None:
    body = run(client, "ecb_rates").json()
    assert body["status"] == "succeeded" and body["run_id"] == 1
    assert body["summary"] == {
        "as_of": "2026-03-02",
        "stored": 3,
        "skipped": 1,
    }  # ILS is not supported
    admin = hdr(client, Role.TENANT_ADMIN)
    quote = client.post(
        "/v1/convert",
        headers=admin,
        json={"amount": "100", "from": "EUR", "to": "USD", "on": "2026-03-02"},
    )
    assert quote.status_code == 200, quote.text
    assert "125.00" in quote.text  # 100 EUR x 1.2500


def test_second_run_waits_and_then_reports_unchanged(client: TestClient, clock: FixedClock) -> None:
    assert run(client, "ecb_rates").json()["status"] == "succeeded"
    assert run(client, "ecb_rates").status_code == 409  # too soon (rule C3)
    clock.advance(minutes=6)
    again = run(client, "ecb_rates").json()
    assert again["status"] == "unchanged" and again["run_id"] == 2


def test_stale_future_or_broken_files_fail_cleanly(
    client: TestClient, clock: FixedClock, fetcher: FakeFetcher
) -> None:
    cases = {
        "stale": ecb_xml("2026-01-01"),
        "future": ecb_xml("2026-03-09"),
        "garbage": b"<html>not rates</html>",
        "no_supported": ecb_xml("2026-03-02", {"ILS": "3.9", "THB": "38.1"}),
    }
    for payload in cases.values():
        fetcher.answers[ECB_URL] = payload
        clock.advance(minutes=6)
        body = run(client, "ecb_rates").json()
        assert body["status"] == "failed" and set(body["summary"]) == {"error"}
    fetcher.answers[ECB_URL] = UpstreamError("down https://www.ecb.europa.eu/?token=abc")
    clock.advance(minutes=6)
    body = run(client, "ecb_rates").json()
    assert body["summary"] == {"error": "upstream_error"}  # no URL, no detail
    runs = client.get("/v1/connectors/runs", headers=hdr(client, Role.TENANT_ADMIN)).json()
    assert [r["status"] for r in runs] == ["failed"] * 5


def test_eurlex_watch_creates_alerts_once(
    client: TestClient, clock: FixedClock, fetcher: FakeFetcher
) -> None:
    first = run(client, "eurlex_watch").json()
    assert first["summary"] == {"items_seen": 2, "new_alerts": 2, "skipped": 0}
    clock.advance(minutes=6)
    assert run(client, "eurlex_watch").json()["status"] == "unchanged"
    fetcher.answers[FEED_URL] = rss(
        ("Regulation (EU) 2026/100", celex_link("32026R0100"), ""),
        ("Regulation (EU) 2026/102", celex_link("32026R0102"), ""),
    )
    clock.advance(minutes=6)
    assert run(client, "eurlex_watch").json()["summary"]["new_alerts"] == 1
    alerts = client.get("/v1/source-alerts", headers=hdr(client, Role.ANALYST)).json()
    assert [a["item_key"] for a in alerts] == [
        "celex:32026R0100",
        "celex:32026L0101",
        "celex:32026R0102",
    ]
    assert alerts[0]["status"] == "open" and alerts[0]["url"].startswith(
        "https://eur-lex.europa.eu/"
    )


def test_alerts_are_acknowledged_by_the_right_roles_once(client: TestClient) -> None:
    run(client, "eurlex_watch")

    def ack(role: Role, i: int = 1):  # type: ignore[no-untyped-def]
        return client.post(f"/v1/source-alerts/{i}/acknowledge", headers=hdr(client, role))

    assert ack(Role.ANALYST).status_code == 403
    assert ack(Role.VIEWER).status_code == 403
    done = ack(Role.COMPLIANCE_OFFICER)
    assert done.status_code == 200 and done.json()["acknowledged_by"] == "user:u1"
    assert ack(Role.TENANT_ADMIN).status_code == 409  # already done
    assert ack(Role.TENANT_ADMIN, 99).status_code == 404
    open_only = client.get(
        "/v1/source-alerts?status=open", headers=hdr(client, Role.ANALYST)
    ).json()
    assert [a["alert_id"] for a in open_only] == [2]
    assert (
        client.get("/v1/source-alerts?status=bogus", headers=hdr(client, Role.ANALYST)).status_code
        == 422
    )


def test_permissions(client: TestClient) -> None:
    for role in (Role.VIEWER, Role.ANALYST, Role.COMPLIANCE_OFFICER, Role.INGESTION_SERVICE):
        assert run(client, "ecb_rates", role).status_code == 403
    assert client.get("/v1/connectors/runs", headers=hdr(client, Role.VIEWER)).status_code == 403
    assert client.get("/v1/connectors/runs", headers=hdr(client, Role.ANALYST)).status_code == 200
    assert (
        client.get("/v1/source-alerts", headers=hdr(client, Role.INGESTION_SERVICE)).status_code
        == 403
    )
    assert client.post("/v1/connectors/ecb_rates/run").status_code == 401
    assert run(client, "nope_connector").status_code == 404
    assert (
        client.post(
            "/v1/connectors/Bad-Name/run", headers=hdr(client, Role.TENANT_ADMIN)
        ).status_code
        == 422
    )


def test_tenants_are_separate(client: TestClient, clock: FixedClock) -> None:
    run(client, "ecb_rates", tenant="acme")
    run(client, "eurlex_watch", tenant="acme")
    other = hdr(client, Role.TENANT_ADMIN, "globex")
    assert client.get("/v1/connectors/runs", headers=other).json() == []
    assert client.get("/v1/source-alerts", headers=other).json() == []
    # the rate limit is per tenant too: globex can run straight away
    assert run(client, "ecb_rates", tenant="globex").json()["run_id"] == 1
    assert (
        client.post(
            "/v1/source-alerts/1/acknowledge", headers=hdr(client, Role.TENANT_ADMIN, "globex")
        ).status_code
        == 404
    )


def test_connector_without_url_is_off(clock: FixedClock, fetcher: FakeFetcher) -> None:
    with client_with(clock, connector_fetcher=fetcher, eurlex_feed_url="") as c:
        assert run(c, "eurlex_watch").status_code == 422
        assert run(c, "ecb_rates").status_code == 200


def test_history_and_audit(client: TestClient, clock: FixedClock) -> None:
    run(client, "ecb_rates")
    clock.advance(minutes=6)
    run(client, "eurlex_watch")
    h = hdr(client, Role.TENANT_ADMIN)
    all_runs = client.get("/v1/connectors/runs", headers=h).json()
    assert [(r["run_id"], r["connector"]) for r in all_runs] == [
        (1, "ecb_rates"),
        (2, "eurlex_watch"),
    ]
    only = client.get("/v1/connectors/runs?connector=eurlex_watch", headers=h).json()
    assert [r["run_id"] for r in only] == [2]
    assert client.get("/v1/connectors/runs?after=1&limit=1", headers=h).json()[0]["run_id"] == 2
    assert client.get("/v1/connectors/runs?connector=zzz", headers=h).status_code == 422
    actions = {e["action"] for e in client.get("/v1/audit", headers=h).json()}
    assert {"connector.run", "fx.rates_stored"} <= actions
    assert client.get("/v1/audit/verify", headers=h).json()["ok"] is True


def test_history_cannot_be_rewritten(client: TestClient) -> None:
    from tests.conftest import DB_ERROR

    run(client, "eurlex_watch")
    db = client.app.state.services.db  # type: ignore[attr-defined]
    for sql in (
        "UPDATE connector_runs SET status = 'failed'",
        "DELETE FROM connector_runs",
        "UPDATE source_alerts SET title = 'changed'",
        "UPDATE source_alerts SET url = 'https://evil.example'",
        "DELETE FROM source_alerts",
    ):
        with pytest.raises(DB_ERROR):
            db.execute(sql)
    assert db.execute("UPDATE source_alerts SET status = 'acknowledged' WHERE alert_id = 1") == 1
    drop_trigger(db, "alert_no_delete", "source_alerts")  # an insider removing the guard
    assert db.execute("DELETE FROM source_alerts WHERE alert_id = 2") == 1
    assert (
        client.get("/v1/audit/verify", headers=hdr(client, Role.TENANT_ADMIN)).json()["ok"] is True
    )


def test_schema_tables_exist(client: TestClient) -> None:
    from tests.conftest import table_names

    assert {"connector_runs", "source_alerts"} <= table_names(client.app.state.services.db)  # type: ignore[attr-defined]
