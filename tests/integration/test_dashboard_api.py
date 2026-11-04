"""Management dashboard: role-shaped sections, tenant isolation, page and security headers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reggraph.core.clock import FixedClock
from reggraph.security.rbac import Principal, Role
from tests.phase3 import BANK, CORP, CORP2, INS, batch, contract, pos, put_contract, register_all
from tests.phase5 import ECB_URL, FEED_URL, FakeFetcher, celex_link, client_with, ecb_xml, rss


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


@pytest.fixture
def client(clock: FixedClock) -> Iterator[TestClient]:
    fetcher = FakeFetcher(
        {ECB_URL: ecb_xml("2026-03-02"), FEED_URL: rss(("Act", celex_link("32026R0100"), ""))}
    )
    with client_with(clock, connector_fetcher=fetcher) as c:
        yield c


def hdr(client: TestClient, role: Role, tenant: str = "acme", user: str = "u1") -> dict[str, str]:
    signer = client.app.state.services.signer  # type: ignore[attr-defined]
    return {"Authorization": f"Bearer {signer.issue(Principal(user, tenant, frozenset({role})))}"}


@pytest.fixture
def world(client: TestClient) -> TestClient:
    analyst, admin = hdr(client, Role.ANALYST), hdr(client, Role.TENANT_ADMIN)
    register_all(client, analyst)
    client.post("/v1/connectors/ecb_rates/run", headers=admin)
    client.post("/v1/connectors/eurlex_watch/run", headers=admin)
    assert put_contract(client, analyst, contract("P-1", INS, BANK, "1000")).status_code == 200
    loaded = client.post(
        "/v1/exposures/batches",
        headers=analyst,
        json=batch(
            pos("L1", BANK, CORP, "300"),
            pos("L2", BANK, CORP2, "100", currency="USD"),  # 80.00 EUR
            pos("L3", BANK, INS, "20"),
        ),
    )
    assert loaded.status_code == 201, loaded.text
    scenario = {
        "scenario_key": "SC-1",
        "title": "Default",
        "description": "t",
        "shocks": [{"kind": "default", "leis": [CORP], "loss_given_default_percent": "50"}],
        "use_insurance": True,
    }
    assert (
        client.put(
            "/v1/stress/scenarios/SC-1",
            headers=analyst,
            json={"scenario": scenario, "change_summary": "x"},
        ).status_code
        == 200
    )
    ran = client.post(
        "/v1/stress/runs", headers=analyst, json={"scenario_key": "SC-1", "as_of": "2026-03-02"}
    )
    assert ran.status_code == 201, ran.text
    return client


def dash(client: TestClient, role: Role, query: str = "", tenant: str = "acme"):  # type: ignore[no-untyped-def]
    return client.get(f"/v1/dashboard{query}", headers=hdr(client, role, tenant))


def test_admin_sees_every_section(world: TestClient) -> None:
    body = dash(world, Role.TENANT_ADMIN).json()
    assert body["view"] == "administration" and body["omitted"] == []
    s = body["sections"]
    assert set(s) == {"registry", "exposure", "insurance", "stress", "rules", "rates", "connectors"}
    assert s["registry"]["entities"] == 7 and s["registry"]["by_type"]["bank"] == 2
    assert s["exposure"]["positions"] == 3 and s["exposure"]["total"] == "400.00"
    top = s["exposure"]["top_counterparties"]
    assert (
        top[0]["lei"] == CORP
        and top[0]["amount"] == "300.00"
        and top[0]["share_percent"] == "75.00"
    )
    assert top[0]["name"].startswith("corporate")
    assert s["insurance"] == {"contracts": 1, "by_line": {"non_life": 1}}
    assert s["stress"]["runs"] == 1 and s["stress"]["recent_runs"][0]["scenario_key"] == "SC-1"
    assert s["rates"] == {"latest_date": "2026-03-02", "currencies": 3}
    assert s["connectors"]["open_alerts"] == 1
    assert s["connectors"]["last_runs"]["ecb_rates"]["status"] == "succeeded"
    assert s["rules"]["versions_by_status"] == {}
    assert body["labels"]["dashboard.title"] == "Management dashboard"


def test_money_follows_the_requested_currency(world: TestClient) -> None:
    eur = dash(world, Role.ANALYST).json()["sections"]["exposure"]["total"]
    usd = dash(world, Role.ANALYST, "?currency=usd").json()["sections"]["exposure"]["total"]
    assert (eur, usd) == ("400.00", "500.00")  # 1.25 USD per EUR
    pln = dash(world, Role.ANALYST, "?currency=PLN").json()["sections"]["exposure"]["total"]
    assert pln == "1680.00"  # 4.2 PLN per EUR


def test_each_role_gets_its_own_view(world: TestClient) -> None:
    expected = {
        Role.VIEWER: ("overview", {"exposure", "insurance", "stress", "connectors"}),
        Role.ANALYST: ("analysis", set()),
        Role.COMPLIANCE_OFFICER: ("compliance", set()),
        Role.TENANT_ADMIN: ("administration", set()),
    }
    for role, (view, omitted) in expected.items():
        body = dash(world, role).json()
        assert body["view"] == view, role
        assert set(body["omitted"]) == omitted, role
        assert not (omitted & set(body["sections"]))
    viewer = dash(world, Role.VIEWER).json()["sections"]
    assert set(viewer) == {"registry", "rules", "rates"}
    assert "open_review_tasks" not in viewer["rules"]  # needs tasks:read
    assert "open_review_tasks" in dash(world, Role.ANALYST).json()["sections"]["rules"]


def test_machine_accounts_cannot_use_the_dashboard(world: TestClient) -> None:
    assert dash(world, Role.INGESTION_SERVICE).status_code == 403
    assert world.get("/v1/dashboard").status_code == 401


def test_a_missing_rate_breaks_only_its_own_section(world: TestClient) -> None:
    body = dash(world, Role.ANALYST, "?currency=CHF").json()  # no CHF rate stored
    assert body["sections"]["exposure"] == {"error": "rate_unavailable"}
    assert body["sections"]["registry"]["entities"] == 7


def test_other_tenants_see_nothing(world: TestClient) -> None:
    body = dash(world, Role.TENANT_ADMIN, tenant="globex").json()
    s = body["sections"]
    assert s["registry"]["entities"] == 0
    assert s["exposure"]["positions"] == 0 and s["exposure"]["total"] == "0.00"
    assert s["insurance"]["contracts"] == 0 and s["stress"]["runs"] == 0
    assert s["rates"] == {"latest_date": None, "currencies": 0}
    assert s["connectors"]["open_alerts"] == 0


@pytest.mark.parametrize(
    "query",
    ["?currency=EURO", "?currency=E1", "?currency=", "?as_of=2026-02-30", "?as_of=yesterday"],
)
def test_bad_parameters_are_refused(world: TestClient, query: str) -> None:
    assert dash(world, Role.ANALYST, query).status_code == 422


def test_unknown_currency_is_a_section_error_not_a_crash(world: TestClient) -> None:
    body = dash(world, Role.ANALYST, "?currency=XXX").json()
    assert body["sections"]["exposure"] == {"error": "invalid_input"}


def test_labels_follow_the_callers_language(world: TestClient) -> None:
    h = {**hdr(world, Role.VIEWER), "Accept-Language": "de-CH"}
    labels = world.get("/v1/dashboard", headers=h).json()["labels"]
    assert labels["dashboard.title"] == "Management-Dashboard"
    assert labels["dashboard.unavailable"].startswith("Für Ihre Rolle")


def test_page_files_are_served_with_a_strict_policy(world: TestClient) -> None:
    page = world.get("/dashboard")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    csp = page.headers["content-security-policy"]
    assert (
        "script-src 'self'" in csp
        and "unsafe-inline" not in csp
        and "frame-ancestors 'none'" in csp
    )
    assert "http" not in csp  # no third-party hosts at all
    assert page.headers["cache-control"] == "no-store"
    assert "<script>" not in page.text and "onclick" not in page.text  # no inline code
    script = world.get("/dashboard/app.js")
    assert script.headers["content-type"].startswith("text/javascript")
    assert "innerHTML" not in script.text and "eval(" not in script.text
    assert "localStorage" not in script.text and "sessionStorage" not in script.text
    assert world.get("/dashboard/app.css").status_code == 200
    icon = world.get("/dashboard/icon.svg")
    assert icon.status_code == 200 and icon.headers["content-type"].startswith("image/svg+xml")
    assert "<script" not in icon.text and "onload" not in icon.text  # a picture, never code
    assert "img-src 'self'" in icon.headers["content-security-policy"]
    assert "style=" not in page.text and "style=" not in script.text.replace(".style.", "")
    # every other route keeps the locked-down policy
    other = world.get("/health").headers["content-security-policy"]
    assert other == "default-src 'none'; frame-ancestors 'none'"


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/../etc/passwd",
        "/dashboard/app.js/..",
        "/dashboard/x",
        "/dashboard/%2e%2e/secret",
        "/dashboard//app.js",
    ],
)
def test_page_routes_do_not_reach_the_file_system(world: TestClient, path: str) -> None:
    resp = world.get(path)
    assert resp.status_code in {200, 307, 308, 404} and "root:" not in resp.text


# ---------------------------------------------------------------- live updates (release 0.6.0)
def test_version_endpoint_is_public_tiny_and_uncacheable(client: TestClient) -> None:
    import reggraph

    res = client.get("/dashboard/version")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"version", "build"} and body["version"] == reggraph.__version__
    assert re.fullmatch(r"[0-9a-f]{12}", body["build"])
    assert res.headers["cache-control"] == "no-store"
    assert client.get("/dashboard/version").json() == body  # stable while nothing changes


def test_language_script_is_served_with_the_page_policy(client: TestClient) -> None:
    res = client.get("/dashboard/lang.js")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/javascript")
    assert "script-src 'self'" in res.headers["content-security-policy"]
    assert "RG_I18N" in res.text


def test_a_changed_page_file_is_served_without_a_restart(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from reggraph.api import routes
    from reggraph.dashboard import STATIC_DIR

    copy = tmp_path / "static"
    shutil.copytree(STATIC_DIR, copy)
    monkeypatch.setattr(routes, "STATIC_DIR", copy)
    monkeypatch.setattr(routes, "_PAGE_CACHE", {})
    first_build = client.get("/dashboard/version").json()["build"]
    first = client.get("/dashboard/app.js").text
    (copy / "app.js").write_text(first + "\n// new release\n", encoding="utf-8")
    assert client.get("/dashboard/app.js").text.endswith("// new release\n")  # fresh from disk
    assert client.get("/dashboard/version").json()["build"] != first_build  # pages notice it


def test_the_overview_payload_lists_languages_and_the_build(world: TestClient) -> None:
    body = world.get("/v1/dashboard", headers=hdr(world, Role.TENANT_ADMIN)).json()
    tags = {entry["tag"] for entry in body["languages"]}
    assert {"en", "de", "fr", "it", "es", "pl", "de-CH", "zh-SG"} <= tags and len(tags) == 34
    assert body["build"] == world.get("/dashboard/version").json()["build"]
    assert body["locale"] == "en" and body["version"]


def test_the_notice_follows_the_language_header(world: TestClient) -> None:
    admin = hdr(world, Role.TENANT_ADMIN)
    en = world.get("/v1/dashboard", headers=admin).json()["labels"]["notice.decision_support"]
    fr = world.get("/v1/dashboard", headers={**admin, "Accept-Language": "fr"}).json()
    assert fr["locale"] == "fr" and fr["labels"]["notice.decision_support"] != en
    junk = world.get("/v1/dashboard", headers={**admin, "Accept-Language": "xx-<script>"}).json()
    assert junk["locale"] == "en"  # an unknown or hostile tag falls back to English
