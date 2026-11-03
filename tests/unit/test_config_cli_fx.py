"""Settings validation, CLI, FX rate book input checks, release hygiene."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from reggraph import __version__
from reggraph.cli import main
from reggraph.config import Settings
from reggraph.core.clock import FixedClock
from reggraph.core.errors import ForbiddenError, ValidationFailure
from reggraph.fx import RateBook
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Principal, Role
from reggraph.storage import Database

GOOD = "Zk3p9Qw7Lm2Xv8Bn4Tr6Yh1Uc5Ja0Sd7Fe"
ROOT = Path(__file__).resolve().parents[2]


def _env(**over: str) -> dict[str, str]:
    base = {"REGGRAPH_ENV": "test", "REGGRAPH_TOKEN_KEYS": f"k1:{GOOD}"}
    base.update(over)
    return base


def test_settings_from_env_ok() -> None:
    cfg = Settings.from_env(_env())
    assert cfg.active_kid == "k1" and cfg.docs_enabled


@pytest.mark.parametrize(
    "env",
    [
        {},  # no keys at all
        _env(REGGRAPH_TOKEN_KEYS="k1:short"),
        _env(REGGRAPH_TOKEN_KEYS="nocolon"),
        _env(REGGRAPH_ENV="staging"),
        _env(REGGRAPH_ENV="prod"),  # in-memory db in prod
        _env(
            REGGRAPH_ENV="prod",
            REGGRAPH_DB_PATH="/var/lib/reggraph/x.db",
            REGGRAPH_TOKEN_KEYS="k1:" + "change-me-" * 5,
        ),
        _env(REGGRAPH_RATE_BURST="0"),
        _env(REGGRAPH_MAX_BODY_BYTES="5"),
        _env(REGGRAPH_ACTIVE_KID="ghost"),
    ],
)
def test_settings_refuse_unsafe_values(env: dict[str, str]) -> None:
    with pytest.raises(ValidationFailure):
        Settings.from_env(env)


def test_cli_issue_token_and_prod_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key, value in _env().items():
        monkeypatch.setenv(key, value)
    code = main(["issue-token", "--tenant", "acme", "--user", "ana", "--role", "analyst"])
    out = capsys.readouterr().out.strip()
    assert code == 0 and out.startswith("v1.k1.")
    monkeypatch.setenv("REGGRAPH_ENV", "prod")
    monkeypatch.setenv("REGGRAPH_DB_PATH", "/var/lib/reggraph/test.db")
    assert main(["issue-token", "--tenant", "acme", "--user", "ana", "--role", "analyst"]) == 2


def _book() -> tuple[RateBook, Principal, Principal]:
    clock = FixedClock(datetime(2026, 3, 2, 9, tzinfo=UTC))
    db = Database()
    book = RateBook(db, AuditLog(db, clock), clock)
    admin = Principal("root", "acme", frozenset({Role.TENANT_ADMIN}))
    analyst = Principal("ana", "acme", frozenset({Role.ANALYST}))
    return book, admin, analyst


def test_ratebook_accepts_and_serves_rates() -> None:
    book, admin, _ = _book()
    assert book.put(admin, date(2026, 3, 2), "ECB", {"USD": "1.1", "SEK": "11.2"}) == 2
    quote = book.provider_for("acme").per_eur("SEK", date(2026, 3, 3))
    assert quote is not None and str(quote.per_eur) == "11.2"
    assert book.provider_for("other").per_eur("SEK", date(2026, 3, 3)) is None


@pytest.mark.parametrize(
    "rates",
    [
        {"USD": "0"},
        {"USD": "-1"},
        {"USD": "abc"},
        {"USD": "1e3"},
        {"USD": " 1.1"},
        {"USD": "\uff11.\uff11"},
        {"USD": "9" * 5000},
        {"ZZZ": "1.1"},
        {"USD": "1.12345678901"},
        {"USD": "99999999"},
        {},
        {"EUR": "1"},
    ],
)
def test_ratebook_rejects_bad_rates(rates: dict[str, str]) -> None:
    book, admin, _ = _book()
    with pytest.raises(ValidationFailure):
        book.put(admin, date(2026, 3, 2), "ECB", rates)


def test_ratebook_rejects_future_dates_and_non_admins() -> None:
    book, admin, analyst = _book()
    with pytest.raises(ValidationFailure):
        book.put(admin, date(2026, 4, 1), "ECB", {"USD": "1.1"})
    with pytest.raises(ForbiddenError):
        book.put(analyst, date(2026, 3, 2), "ECB", {"USD": "1.1"})


def test_rates_are_insert_only() -> None:
    book, admin, _ = _book()
    book.put(admin, date(2026, 3, 2), "ECB", {"USD": "1.1"})
    with pytest.raises(Exception, match=r"insert-only"):
        book._db.execute("UPDATE fx_rates SET per_eur = '9'")


def test_version_file_matches_package() -> None:
    assert (ROOT / "VERSION").read_text().strip() == __version__


def test_every_module_is_versioned_and_unchanged() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "module_manifest", ROOT / "tools/module_manifest.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.uncovered_files() == []
    assert mod.check() == 0
