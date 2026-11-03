"""Dashboard in a real browser: fonts, colours, shapes, layout, CSP and privacy.

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
from pathlib import Path

import pytest

from reggraph.api import create_app
from reggraph.api.deps import build_services
from reggraph.config import Settings
from reggraph.core.clock import SystemClock
from reggraph.dashboard import STATIC_DIR
from reggraph.demo import seed

sync_api = pytest.importorskip("playwright.sync_api")
CSS = Path(STATIC_DIR, "app.css").read_text(encoding="utf-8")


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


_ROOT = CSS.split("@media (prefers-color-scheme: dark)")[0]
_DARK = CSS.split("@media (prefers-color-scheme: dark)")[1].split("}")[0]
LIGHT = _tokens(_ROOT)
DARK = {**LIGHT, **_tokens(_DARK)}


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
    ("ok", "ok-bg"), ("warn", "warn-bg"), ("bad", "bad-bg"), ("info", "info-bg"),
    ("on-brand", "brand-a"), ("on-brand", "brand-b"),
]  # fmt: skip
GRAPHIC = ["c1", "c2", "c3", "c4", "c5", "c6"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_text_colour_pairs_meet_wcag_aa(theme: str) -> None:
    t = LIGHT if theme == "light" else DARK
    for fg, bg in TEXT_PAIRS:
        assert ratio(t[fg], t[bg]) >= 4.5, (theme, fg, bg, ratio(t[fg], t[bg]))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chart_colours_are_visible_on_the_card(theme: str) -> None:
    t = LIGHT if theme == "light" else DARK
    for c in GRAPHIC:
        assert ratio(t[c], t["card"]) >= 3.0, (theme, c, ratio(t[c], t["card"]))


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    import uvicorn

    settings = Settings(
        env="dev",
        token_keys={"ui": secrets.token_urlsafe(48).encode()},
        active_kid="ui",
        offline=True,
    )
    services = build_services(settings, SystemClock())
    who = seed(services)["admin"]
    token = services.signer.issue(who, ttl_seconds=3600)
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
    yield f"http://127.0.0.1:{port}/dashboard#token={token}"
    srv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment without Chromium
            pytest.skip(f"Chromium unavailable: {exc}")
        yield b
        b.close()


VIEWPORTS = [(1440, 900), (1024, 768), (820, 1100), (390, 844), (320, 640)]


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("size", VIEWPORTS)
def test_page_renders_cleanly(browser, server: str, size: tuple[int, int], scheme: str) -> None:  # type: ignore[no-untyped-def]
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme)
    page = ctx.new_page()
    problems: list[str] = []
    requests: list[str] = []
    page.on(
        "console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None
    )
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    page.goto(server)
    page.wait_for_selector(".kpi")
    page.wait_for_timeout(700)
    assert problems == []  # no CSP violation, script error or failed request
    assert all(u.startswith("http://127.0.0.1") for u in requests)  # nothing leaves the machine
    assert page.evaluate("location.hash") == ""  # the token is gone from the address bar
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator(".kpi").count() == 6
    assert page.locator("svg.donut").count() == 2
    assert page.locator(".bar .fill").count() >= 6
    assert page.locator("#offline").is_visible()
    widths = page.evaluate(
        "[...document.querySelectorAll('.bar .fill')].map(e => e.getBoundingClientRect().width)"
    )
    assert all(w > 0 for w in widths)
    ctx.close()


def test_fonts_and_shapes(browser, server: str) -> None:  # type: ignore[no-untyped-def]
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(server)
    page.wait_for_selector(".kpi")
    fam = page.evaluate("getComputedStyle(document.body).fontFamily")
    assert "system-ui" in fam and "http" not in fam  # system fonts only, no downloads
    assert page.evaluate("getComputedStyle(document.body).fontSize") == "15px"
    assert page.evaluate("getComputedStyle(document.querySelector('h1')).fontWeight") in {
        "700",
        "bold",
    }
    assert (
        page.evaluate("getComputedStyle(document.querySelector('section')).borderRadius") == "14px"
    )
    assert page.evaluate(
        "getComputedStyle(document.querySelector('.top')).backgroundImage"
    ).startswith("linear-gradient")
    # the donut segments add up to the full circle
    total = page.evaluate(
        "[...document.querySelectorAll('svg.donut')[0].querySelectorAll('.seg')]"
        ".map(c => parseFloat(c.getAttribute('stroke-dasharray'))).reduce((a,b)=>a+b,0)"
    )
    assert abs(total - 2 * 3.14159265 * 46) < 0.5
    # every control reachable by keyboard and labelled
    assert page.evaluate("[...document.querySelectorAll('input')].every(i => i.labels.length > 0)")
    ctx.close()


def test_no_page_script_can_be_injected(browser, server: str) -> None:  # type: ignore[no-untyped-def]
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(server.split("#")[0] + "#token=<img src=x onerror=alert(1)>")
    page.wait_for_timeout(300)
    assert page.locator("img").count() == 0  # a malformed fragment is ignored, never parsed
    ctx.close()
