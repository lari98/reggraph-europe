"""Dashboard console in a real browser: colours, fonts, layout, drill-down, roles, privacy.

Needs Chromium through Playwright; the tests skip themselves when it is not installed.
Every foreground/background pair of the style sheet is checked against WCAG 2.1 AA.
"""

from __future__ import annotations

import re
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from reggraph.api import create_app
from reggraph.api.deps import build_services
from reggraph.config import Settings
from reggraph.core.clock import SystemClock
from reggraph.dashboard import STATIC_DIR
from reggraph.demo import L, seed

sync_api = pytest.importorskip("playwright.sync_api")
CSS = Path(STATIC_DIR, "app.css").read_text(encoding="utf-8")


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


def _block(start: str) -> str:
    i = CSS.index(start)
    return CSS[i : CSS.index("}", i)]


LIGHT = _tokens(_block(":root {"))
DARK = {**LIGHT, **_tokens(_block(':root[data-theme="dark"] {'))}
DARK_MEDIA = {**LIGHT, **_tokens(_block(':root:not([data-theme="light"]) {'))}


def _lum(hex_: str) -> float:
    def ch(i: int) -> float:
        v = int(hex_[i : i + 2], 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5)


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("ink", "card"), ("ink", "bg"), ("muted", "card"), ("muted", "bg"),
    ("accent", "card"), ("accent", "bg"), ("accent-soft-ink", "accent-soft"),
    ("on-accent", "accent"), ("ink", "accent-soft"), ("ink", "track"),
    ("side-ink", "side-bg"), ("side-muted", "side-bg"), ("side-ink", "side-active"),
    ("side-muted", "side-active"),
    ("ok", "ok-bg"), ("warn", "warn-bg"), ("bad", "bad-bg"), ("info", "info-bg"),
    ("ink", "ok-bg"), ("ink", "warn-bg"), ("ink", "bad-bg"),  # attention list and hero boxes
]  # fmt: skip
GRAPHIC = ["c1", "c2", "c3", "c4", "c5", "c6"]


def test_token_sets_are_complete_and_dark_blocks_agree() -> None:
    assert len(LIGHT) >= 29 and set(LIGHT) == set(DARK)
    assert DARK == DARK_MEDIA  # system-dark and the manual toggle must look identical


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_colour_pairs_meet_wcag_aa(theme: str) -> None:
    t = LIGHT if theme == "light" else DARK
    for fg, bg in TEXT_PAIRS:
        assert ratio(t[fg], t[bg]) >= 4.5, (theme, fg, bg, round(ratio(t[fg], t[bg]), 2))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chart_colours_are_visible_on_the_card(theme: str) -> None:
    t = LIGHT if theme == "light" else DARK
    for c in GRAPHIC:
        assert ratio(t[c], t["card"]) >= 3.0, (theme, c, round(ratio(t[c], t["card"]), 2))
    for c in ("line-strong",):
        assert ratio(t[c], t["card"]) >= 1.3  # input borders stay visible


def test_style_sheet_has_no_external_resources() -> None:
    assert "@import" not in CSS and "url(" not in CSS and "http" not in CSS
    assert "system-ui" in CSS and "font-face" not in CSS  # system fonts only


@pytest.fixture(scope="module")
def server() -> Iterator[dict[str, str]]:
    import uvicorn

    settings = Settings(
        env="dev",
        token_keys={"ui": secrets.token_urlsafe(48).encode()},
        active_kid="ui",
        offline=True,
        rate_burst=2000,
        rate_per_minute=20000,
    )
    services = build_services(settings, SystemClock())
    people = seed(services)
    tokens = {r: services.signer.issue(p, ttl_seconds=3600) for r, p in people.items()}
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, SystemClock(), services=services),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    base = f"http://127.0.0.1:{port}/dashboard"
    yield {"base": base, **{r: f"{base}#token={t}" for r, t in tokens.items()}}
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment without Chromium
            pytest.skip(f"Chromium unavailable: {exc}")
        yield b
        b.close()


def open_page(
    browser: Any,
    url: str,
    *,
    size: tuple[int, int] = (1280, 900),
    scheme: str = "light",
    init_script: str | None = None,
    locale: str = "en-US",
) -> Any:
    ctx = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        color_scheme=scheme,
        accept_downloads=True,
        locale=locale,
    )
    if init_script:
        ctx.add_init_script(init_script)  # test hook, e.g. a fast refresh interval
    page = ctx.new_page()
    page.problems = []  # type: ignore[attr-defined]
    page.requests = []  # type: ignore[attr-defined]
    page.on(
        "console",
        lambda m: page.problems.append(m.text) if m.type in ("error", "warning") else None,
    )
    page.on("pageerror", lambda e: page.problems.append(str(e)))
    page.on("request", lambda r: page.requests.append(r.url))
    page.goto(url)
    return page


def _wait(page: Any, expression: str, what: str, tries: int = 100) -> None:
    """Poll a page expression from Python (``wait_for_function`` is blocked by the page CSP)."""
    for _ in range(tries):
        if page.evaluate(expression):
            return
        page.wait_for_timeout(100)
    raise AssertionError(f"timed out waiting for {what}")


def settle(page: Any) -> None:
    _wait(
        page, "!!document.querySelector('#main') && !document.querySelector('#main .skel')", "page"
    )


def h1(page: Any, prefix: str) -> None:
    """Wait until the page heading starts with ``prefix`` (the route change is asynchronous)."""
    _wait(
        page,
        "((document.querySelector('#main h1') || {textContent: ''}).textContent"
        f".startsWith({prefix!r}))",
        f"heading {prefix!r}",
    )
    settle(page)


def go(page: Any, route: str) -> None:
    page.evaluate(f"location.hash = '#/{route}'")
    page.wait_for_timeout(150)
    settle(page)


ROUTES = [
    "overview", "entities", f"entity/{L['NORDHAFEN']}", f"entity/{L['NORDHAFEN']}/group",
    f"entity/{L['NORDHAFEN']}/exposure", f"entity/{L['NORDHAFEN']}/insurance",
    f"entity/{L['NORDHAFEN']}/rules", f"entity/{L['NORDHAFEN']}/network",
    f"entity/{L['NORDHAFEN']}/history", "exposure", "insurance", "stress", "stress/3",
    "rules", "data",
]  # fmt: skip


def test_every_page_renders_cleanly_and_stays_local(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    for route in ROUTES:
        go(page, route)
        assert page.locator("#main .err").count() == 0, route
        assert page.locator("#main h1").count() == 1, route
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), route
    assert page.problems == []  # no CSP violation, script error or failed request
    assert all(u.startswith("http://127.0.0.1") for u in page.requests)
    page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", [(1440, 900), (1024, 768), (820, 1100), (390, 844), (320, 640)])
def test_overview_layout_at_every_size(
    browser: Any, server: dict[str, str], size: tuple[int, int], scheme: str
) -> None:
    page = open_page(browser, server["admin"], size=size, scheme=scheme)
    page.wait_for_selector(".kpi")
    settle(page)
    page.wait_for_timeout(700)
    assert page.problems == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".kpi").count() == 6
    assert page.locator("svg.donut").count() == 3 and page.locator("svg.net").count() == 1
    widths = page.evaluate(
        "[...document.querySelectorAll('.bar-i .fill')].map(e => e.getBoundingClientRect().width)"
    )
    assert widths and all(w > 0 for w in widths)
    assert page.evaluate("location.hash") == "#/overview"  # the token is gone from the address bar
    assert page.locator("#offline").is_visible()
    page.context.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_overview_pictures_map_gauge_grid_and_bars(
    browser: Any, server: dict[str, str], scheme: str
) -> None:
    """0.7.0: the offline tile map, gauge, country grid, paired bars and plain-language analysis."""
    page = open_page(browser, server["admin"], size=(1440, 900), scheme=scheme)
    page.wait_for_selector(".kpi")
    settle(page)
    page.wait_for_timeout(900)
    assert page.problems == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # map: one tile per country, populated tiles link to the register filtered by that country
    assert page.locator("svg.geomap .tile-r").count() >= 30
    link = page.locator("svg.geomap a.tile").first
    assert "#/country/" in (link.get_attribute("href") or "")
    # gauge, grid and paired bars are drawn; analysis has several readable sentences
    assert page.locator("svg.gauge .needle").count() == 1
    assert page.locator("svg.xgrid rect").count() >= 4
    assert page.locator(".pairs .pair-i").count() >= 2
    assert page.locator(".an li").count() >= 3
    # 0.7.0 second batch: by country, by type, lenders, exposure-to-loss steps, key ratios
    for title in (
        "Money owed to institutions, by country",
        "Who owes the money",
        "Biggest lenders",
        "From exposure to loss",
        "Key ratios at a glance",
    ):
        assert page.locator(".ov .card h2", has_text=title).count() == 1, title
    assert (
        page.locator(".ov .card h2", has_text="Key ratios at a glance")
        .locator("xpath=ancestor::section")
        .locator(".bar-i")
        .count()
        >= 3
    )
    # nothing sticks out of its card
    over = page.evaluate(
        "[...document.querySelectorAll('.ov .card')].filter(c => c.scrollWidth > c.clientWidth + 1).length"
    )
    assert over == 0
    # clicking a country opens the register filtered to it
    link.click()
    _wait(page, "location.hash.startsWith('#/country/')", "country page")
    page.context.close()


def test_overview_pictures_are_translated_and_phone_safe(
    browser: Any, server: dict[str, str]
) -> None:
    page = open_page(browser, server["admin"], size=(390, 844), locale="de-DE")
    page.wait_for_selector(".kpi")
    settle(page)
    page.wait_for_timeout(900)
    assert page.problems == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert "Wo die Institute sitzen" in page.inner_text(".ov")
    assert "Was das bedeutet" in page.inner_text(".an-card")
    page.context.close()


def test_country_page_print_view_and_data_page(browser: Any, server: dict[str, str]) -> None:
    """0.8.0: per-country page, trend chart, print layout and the richer Data & audit page."""
    page = open_page(browser, server["admin"], size=(1440, 900))
    page.wait_for_selector(".kpi")
    settle(page)
    go(page, "country/DE")
    h1(page, "Germany")
    assert page.locator(".kpis-ov .kpi").count() == 5
    assert page.locator(".an li").count() >= 2 and page.locator("tbody tr.link").count() >= 1
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    go(page, "country/ZZ")  # a country without institutions: friendly empty state, no crash
    assert "No institutions are registered" in page.locator("#main").inner_text()
    go(page, "country/not-a-country")  # hostile input is refused, not rendered
    assert page.locator("#main .err").count() == 1
    # trend chart on the stress page (needs two runs; the demo server has three)
    go(page, "stress")
    page.wait_for_selector("svg.lines")
    assert page.locator("svg.lines path.ln").count() == 2
    assert page.locator("svg.lines circle.dt").count() >= 4
    # richer Data & audit page
    go(page, "data")
    page.wait_for_selector(".chain .blk")
    assert page.locator(".chain .blk").count() >= 3
    assert page.locator(".kpis-ov .kpi").count() >= 4
    assert "Intact" in page.locator("#main").inner_text()
    assert page.problems == []
    # print layout: chrome hidden, cards kept, header line shown
    go(page, "overview")
    page.wait_for_selector(".hero")
    page.emulate_media(media="print")
    assert page.locator(".side").is_hidden() and page.locator(".bar").is_hidden()
    assert page.locator(".print-head").is_visible()
    assert page.locator(".print-btn").first.is_hidden()
    page.emulate_media(media="screen")
    assert page.locator(".side").is_visible() and page.locator(".print-head").is_hidden()
    page.context.close()


def _admin_token(server: dict[str, str]) -> str:
    return server["admin"].split("#token=", 1)[1]


def _api(server: dict[str, str], method: str, path: str) -> Any:
    import json
    import urllib.request

    req = urllib.request.Request(  # noqa: S310 - loopback test server
        server["base"].rsplit("/dashboard", 1)[0] + path,
        method=method,
        headers={"Authorization": "Bearer " + _admin_token(server)},
    )
    with urllib.request.urlopen(req, timeout=10) as res:  # noqa: S310  # nosec B310
        return json.loads(res.read() or b"null")


@pytest.mark.parametrize("size", [(1440, 900), (1280, 800), (1024, 768), (820, 1100), (390, 844)])
@pytest.mark.parametrize("lang", ["en-US", "de-DE"])
def test_overview_boxes_are_aligned_and_never_clipped(
    browser: Any, server: dict[str, str], size: tuple[int, int], lang: str
) -> None:
    """Boxes in one row share a top edge and a height; no number is cut off; nothing overflows."""
    page = open_page(browser, server["admin"], size=size, locale=lang)
    page.wait_for_selector(".kpi-ov")
    settle(page)
    page.wait_for_timeout(600)
    rows = page.evaluate(
        """() => {
          const rows = {};
          for (const el of document.querySelectorAll('.kpi-ov, #main .grid > .card')) {
            const r = el.getBoundingClientRect();
            (rows[Math.round(r.top + scrollY)] ||= []).push([r.height, r.left, r.right, r.width]);
          }
          return Object.values(rows);
        }"""
    )
    for row in rows:
        heights = {round(h) for h, *_ in row}
        assert len(heights) == 1, (size, heights)  # equal height inside a row
        lefts = sorted((left, right) for _, left, right, _w in row)
        assert all(a[1] <= b[0] + 1 for a, b in pairwise(lefts))  # no overlap
    clipped = page.evaluate(
        "[...document.querySelectorAll('.kpi-ov .v')].filter(e => e.scrollWidth > e.clientWidth + 1).length"
    )
    assert clipped == 0, (size, lang)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".hero").is_visible() and page.locator(".guide").is_visible()
    page.context.close()


def test_overview_explains_itself_in_plain_words(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi-ov")
    settle(page)
    text = page.locator(".hero").inner_text()
    assert "ACTION NEEDED" in text.upper() and "capital shortfall" in text  # the breach is stated
    assert page.locator(".attn a").count() >= 2  # every attention item is a link
    page.locator(".guide summary").click()
    assert "20-character official ID" in page.locator(".guide").inner_text()
    for kpi_card in page.locator(".kpi-ov").all():  # each box says what it means in a sentence
        assert len(kpi_card.locator(".s").inner_text().split()) >= 4
    page.locator(".attn a").first.click()
    h1(page, "Stress")
    page.context.close()


def test_live_refresh_redraws_in_place_without_reload(browser: Any, server: dict[str, str]) -> None:
    """New data appears by itself: the page is not reloaded and the open guide stays open.

    Runs before the acknowledge test below (module order) so that an open alert still exists.
    """
    page = open_page(browser, server["admin"], init_script="window.__RG_LIVE_MS = 500;")
    page.wait_for_selector(".kpi-ov")
    settle(page)
    page.evaluate("window.__marker = 42")
    page.locator(".guide summary").click()
    before = int(page.locator(".kpi.k6 .v").inner_text())
    assert before >= 1
    alert = next(a for a in _api(server, "GET", "/v1/source-alerts?status=open"))
    _api(server, "POST", f"/v1/source-alerts/{alert['alert_id']}/acknowledge")
    sync_api.expect(page.locator(".kpi.k6 .v")).to_have_text(str(before - 1), timeout=8000)
    assert page.evaluate("window.__marker") == 42  # same document: no reload happened
    assert page.locator(".guide[open]").count() == 1  # the open guide survived the redraw
    assert page.locator("#live-time").inner_text() != ""
    assert page.problems == []
    page.context.close()


def test_live_pill_pauses_and_reconnects(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"], init_script="window.__RG_LIVE_MS = 400;")
    page.wait_for_selector(".kpi-ov")
    live = page.locator("#live")
    assert live.get_attribute("data-state") == "on"
    live.click()
    assert live.get_attribute("data-state") == "paused"
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url) if "/dashboard/version" in r.url else None)
    page.wait_for_timeout(1500)
    assert seen == []  # paused: no request at all
    live.click()
    sync_api.expect(live).to_have_attribute("data-state", "on")
    page.route("**/dashboard/version", lambda route: route.abort())
    sync_api.expect(live).to_have_attribute("data-state", "reconnect", timeout=8000)
    page.unroute("**/dashboard/version")
    sync_api.expect(live).to_have_attribute("data-state", "on", timeout=15000)  # resumes by itself
    page.context.close()


def test_new_release_reloads_page_and_keeps_the_state(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"], init_script="window.__RG_LIVE_MS = 400;")
    page.wait_for_selector(".kpi-ov")
    go(page, "exposure")
    page.evaluate("window.__marker = 1")
    page.get_by_role("button", name=re.compile("Currency")).click()
    page.locator("#ccy-list button", has_text="CHF").first.click()
    page.get_by_role("button", name=re.compile("Language")).click()
    page.locator("#lang-list button", has_text="Deutsch").first.click()
    settle(page)
    page.route(
        "**/dashboard/version",
        lambda route: route.fulfill(json={"version": "9.9.9", "build": "ffffffffffff"}),
    )
    for _ in range(75):  # the page reloads by itself (string scripts are blocked by the page CSP)
        if page.evaluate("window.__marker === undefined"):
            break
        page.wait_for_timeout(200)
    else:
        raise AssertionError("the page did not reload after a new build was announced")
    page.wait_for_selector(".kpi-ov, #main h1")
    settle(page)
    assert page.evaluate("location.hash").startswith("#/exposure")  # same page
    assert page.locator("#ccy-label").inner_text() == "CHF"  # same currency
    assert page.locator("#lang-label").inner_text() == "DE"  # same language
    assert page.locator("#nav a[aria-current=page]").inner_text().strip() == "Risikopositionen"
    assert "token" not in page.evaluate("location.hash")  # the token left the address bar again
    page.context.close()


def test_language_dropdown_translates_and_formats(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi-ov")
    english_notice = page.locator("#notice").inner_text()
    page.get_by_role("button", name=re.compile("Language")).click()
    assert page.locator("#lang-list button").count() >= 30  # all supported languages are listed
    page.locator("#lang-q").fill("deut")
    page.locator("#lang-list button", has_text="Deutsch").first.click()
    sync_api.expect(page.locator("#main h1")).to_have_text("Übersicht")
    assert page.locator("html").get_attribute("lang") == "de"
    assert page.locator("#nav a").nth(2).inner_text().strip() == "Risikopositionen"
    assert page.locator("#date-label").inner_text() == "Heute"
    assert "Mrd" in page.locator(".kpi.k2 .v").inner_text()  # German number format
    assert page.locator("#notice").inner_text() != english_notice  # server-translated notice
    page.get_by_role("button", name=re.compile("Sprache")).click()
    page.locator("#lang-list button", has_text="Italiano").first.click()
    sync_api.expect(page.locator("#main h1")).to_have_text("Panoramica")
    page.get_by_role("button", name=re.compile("Lingua")).click()  # a language without a full text
    page.locator("#lang-q").fill("polski")
    page.locator("#lang-list button", has_text="Polski").first.click()
    sync_api.expect(page.locator("#notice")).not_to_have_text(english_notice)
    assert page.locator("#main h1").inner_text() == "Overview"  # text stays English, honestly
    assert page.locator("#lang-list .chip-neutral").count() >= 1  # the list says which are partial
    assert page.problems == []
    page.context.close()


def test_fragment_parts_are_validated(browser: Any, server: dict[str, str]) -> None:
    tok = _admin_token(server)
    base = server["base"]
    page = open_page(
        browser, f"{base}#token={tok}&r=javascript:alert(1)&l=%3Cb%3E&c=eur&d=2999-01-01&th=evil"
    )
    page.wait_for_selector(".kpi-ov")
    assert page.evaluate("location.hash") == "#/overview"  # hostile route ignored
    assert page.locator("#ccy-label").inner_text() == "EUR"  # lower-case currency ignored
    assert page.locator("#date-label").inner_text() == "Today"  # future day ignored
    assert page.problems == []
    page.context.close()


def test_fonts_and_shapes(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    css = page.evaluate
    fam = css("getComputedStyle(document.body).fontFamily")
    assert "system-ui" in fam and "http" not in fam
    assert css("getComputedStyle(document.body).fontSize") == "14px"
    hero = float(css("parseFloat(getComputedStyle(document.querySelector('#main h1')).fontSize)"))
    assert 24 <= hero <= 32  # overview title scales with the screen
    go(page, "entities")
    assert css("getComputedStyle(document.querySelector('#main h1')).fontSize") == "22px"
    go(page, "overview")
    assert css("getComputedStyle(document.querySelector('.card')).borderRadius") == "14px"
    assert css("getComputedStyle(document.querySelector('.kpi')).borderTopWidth") == "3px"
    assert css("getComputedStyle(document.querySelector('.side')).position") == "sticky"
    total = css(
        "[...document.querySelectorAll('svg.donut')[0].querySelectorAll('.seg')]"
        ".map(c => parseFloat(c.getAttribute('stroke-dasharray'))).reduce((a,b)=>a+b,0)"
    )
    assert abs(total - 2 * 3.14159265 * 46) < 0.5  # the segments close the circle
    assert css(
        "[...document.querySelectorAll('input,select')].every(i => i.labels.length > 0 || i.getAttribute('aria-label'))"
    )
    assert (
        css("document.querySelector('#nav a[aria-current=page]').textContent.trim()") == "Overview"
    )
    page.context.close()


def test_drill_down_from_overview_to_a_record(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    settle(page)
    page.locator(".legend a", has_text="bank").first.click()  # donut legend -> filtered list
    settle(page)
    assert page.evaluate("location.hash").startswith("#/entities?type=bank")
    assert page.locator("select[aria-label=Type]").input_value() == "bank"
    assert page.locator("tbody tr.link").count() == 5
    page.locator("tbody tr.link", has_text="Nordhafen").first.click()  # row -> entity
    h1(page, "Nordhafen Bank")
    page.locator(".tabs a", has_text="Exposure").click()
    page.wait_for_selector("#main .chip:has-text('high concentration')")
    page.locator(".bar-i a", has_text="Kronos").first.click()  # bar -> counterparty entity
    h1(page, "Kronos Industrie")
    page.go_back()
    h1(page, "Nordhafen")
    assert page.problems == []
    page.context.close()


def test_table_sorting_filtering_paging_and_csv(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    go(page, "entities")
    first = lambda: page.locator("tbody tr").first.locator("td").first.inner_text()  # noqa: E731
    page.locator("th button", has_text="Name").click()
    assert page.locator("th[aria-sort=ascending]").count() == 1
    a = first()
    page.locator("th button", has_text="Name").click()
    assert page.locator("th[aria-sort=descending]").count() == 1 and first() != a
    page.locator("input[type=search][aria-label='Filter this table']").fill("tulp")
    assert page.locator("tbody tr.link").count() == 1
    page.locator("input[type=search][aria-label='Filter this table']").fill("")
    assert "1-15 of 15" in page.locator(".pager").inner_text()
    with page.expect_download() as dl:
        page.get_by_role("button", name="Export CSV").click()
    text = Path(dl.value.path()).read_text(encoding="utf-8")
    assert text.splitlines()[0].startswith('"Name","LEI"') and len(text.splitlines()) == 16
    page.context.close()


def test_search_drawer_verify_and_acknowledge(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    page.keyboard.press("Control+k")  # keyboard shortcut focuses the search box
    assert page.evaluate("document.activeElement.id") == "q"
    page.keyboard.type("solaris")
    page.wait_for_selector("#q-list li[role=option]")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    h1(page, "Solaris")
    go(page, "insurance")  # drawer: open with click, close with Escape, focus returns
    row = page.locator("tbody tr.link", has_text=re.compile(r"^\s*D-NL-002"))
    row.locator("td").first.click()
    page.wait_for_selector(".drawer")
    assert "Contract D-NL-002" in page.locator(".drawer").inner_text()
    page.keyboard.press("Escape")
    assert page.locator(".drawer").count() == 0
    go(page, "stress/3")
    page.get_by_role("button", name="Verify this run").click()
    page.wait_for_selector(".chip-ok:has-text('result reproduced: yes')")
    assert page.locator(".chip-bad:has-text('NO')").count() == 0
    go(page, "data")
    assert "chain intact" in page.locator("#main").inner_text()
    assert page.get_by_role("button", name="Run ecb_rates").is_disabled()  # offline: switched off
    open_alerts = page.get_by_role("button", name="Acknowledge").count()
    assert open_alerts >= 1
    page.get_by_role("button", name="Acknowledge").first.click()
    page.wait_for_selector(".toast")
    sync_api.expect(page.get_by_role("button", name="Acknowledge")).to_have_count(open_alerts - 1)
    assert page.problems == []
    page.context.close()


def test_viewer_role_sees_locks_not_errors(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["viewer"])
    page.wait_for_selector(".kpi")
    settle(page)
    assert page.locator(".lock").count() >= 1
    for route in ("exposure", "insurance", "stress", "data"):
        go(page, route)
        assert (
            "Not available for your role" in page.locator("#main").inner_text()
            or page.locator("#main .lock").count() >= 1
        ), route
    go(page, "entities")
    assert page.locator("tbody tr.link").count() == 15  # what the role may read still works
    page.context.close()


def test_sign_in_screen_and_hostile_fragments(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["base"])
    assert page.locator("#signin").is_visible() and page.locator("#app").is_hidden()
    page.locator("#token").fill("v1.bad.token.value")
    page.get_by_role("button", name="Open dashboard").click()
    sync_api.expect(page.locator("#signin-error")).not_to_be_empty()
    assert "not accepted" in page.locator("#signin-error").inner_text()
    page.goto(server["base"] + "#token=<img src=x onerror=alert(1)>")
    assert page.locator("img").count() == 0 and page.locator("#signin").is_visible()
    page.context.close()


def test_sun_switch_changes_theme_and_is_accessible(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"], scheme="light")
    page.wait_for_selector(".kpi")
    sw = page.get_by_role("switch")
    assert sw.get_attribute("aria-checked") == "true" and "Light theme" in sw.get_attribute(
        "aria-label"
    )
    light = page.evaluate("getComputedStyle(document.body).backgroundColor")
    glow = page.evaluate("getComputedStyle(document.querySelector('.theme-knob')).boxShadow")
    assert "rgba(251, 191, 36" in glow or "rgb(251, 191, 36" in glow  # the sun glows in light mode
    sw.click()
    assert sw.get_attribute("aria-checked") == "false"
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") != light
    page.wait_for_timeout(500)  # the glow fades with a short transition
    assert "251, 191, 36" not in page.evaluate(
        "getComputedStyle(document.querySelector('.theme-knob')).boxShadow"
    )
    sw.focus()
    page.keyboard.press("Space")  # keyboard works too
    assert sw.get_attribute("aria-checked") == "true"
    page.context.close()


def test_currency_dropdown(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    page.get_by_role("button", name=re.compile("Currency")).click()
    page.wait_for_selector("#ccy-list button")
    assert page.locator("#ccy-list button").count() >= 10
    assert (
        page.locator("#ccy-list button").first.inner_text().count("EUR") == 1
    )  # common ones come first
    page.locator("#ccy-q").fill("franc")
    assert page.locator("#ccy-list button").count() >= 1
    page.locator("#ccy-list button", has_text="CHF").first.click()
    assert page.locator("#ccy-label").inner_text() == "CHF"
    sync_api.expect(page.locator(".kpi.k2")).to_contain_text("CHF")  # amounts follow the choice
    assert page.locator("#ccy-pop").is_hidden()
    page.get_by_role("button", name=re.compile("Currency")).click()
    page.keyboard.press("Escape")
    assert page.locator("#ccy-pop").is_hidden()
    assert page.problems == []
    page.context.close()


def test_date_presets_and_explanation(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    assert page.locator("#date-label").inner_text() == "Today"
    page.get_by_role("button", name=re.compile("Data as of")).click()
    assert "as it stood on a past day" in page.locator("#date-pop").inner_text()
    page.locator("#date-presets button", has_text="Yesterday").click()
    assert page.locator("#date-label").inner_text() != "Today"
    settle(page)
    assert page.locator("#main .err").count() == 0
    page.get_by_role("button", name=re.compile("Data as of")).click()
    page.locator("#date-presets button", has_text="Today").click()
    assert page.locator("#date-label").inner_text() == "Today"
    page.context.close()


def test_header_menu_sign_out_wipes_state(browser: Any, server: dict[str, str]) -> None:
    page = open_page(browser, server["admin"])
    page.wait_for_selector(".kpi")
    page.get_by_role("button", name="Account menu").click()
    assert "Signed in as" in page.locator("#user-name").inner_text()
    page.get_by_role("menuitem", name="Sign out").click()
    assert page.locator("#signin").is_visible() and page.locator("#token").input_value() == ""
    page.context.close()
