"""Money, conversion and formatting (module: currency)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from reggraph.core.clock import FixedClock
from reggraph.core.errors import RateUnavailableError, ValidationFailure
from reggraph.currency import Converter, InMemoryRates, Money, format_money, get_currency

TODAY = date(2026, 3, 2)


def _rates() -> InMemoryRates:
    rates = InMemoryRates()
    for code, value in {"USD": "1.10", "CZK": "25.00", "SEK": "11.00", "CHF": "0.95"}.items():
        rates.add(code, Decimal(value), TODAY, "test")
    return rates


def _conv(day: date = TODAY) -> Converter:
    clock = FixedClock(datetime(day.year, day.month, day.day, 9, tzinfo=UTC))
    return Converter(_rates(), clock)


def test_non_euro_eu_currencies_are_supported() -> None:
    for code in ("CZK", "DKK", "HUF", "PLN", "RON", "SEK"):
        assert get_currency(code) is not None


def test_rounding_is_half_even() -> None:
    assert Money.of("0.125", "EUR").amount == Decimal("0.12")
    assert Money.of("0.135", "EUR").amount == Decimal("0.14")


def test_money_rejects_float_style_and_exponent_input() -> None:
    for bad in ("1e9999", "NaN", "Infinity", "1_0", "0x10", "", " "):
        with pytest.raises(ValidationFailure):
            Money.of(bad, "EUR")


def test_money_cap() -> None:
    with pytest.raises(ValidationFailure):
        Money.of("1000000000000000", "EUR")


def test_cross_currency_add_is_refused() -> None:
    with pytest.raises(ValidationFailure):
        Money.of("1", "EUR") + Money.of("1", "CZK")


def test_triangulation_via_eur() -> None:
    result = _conv().convert(Money.of("100", "USD"), "CZK")
    assert result.result.currency == "CZK"
    assert result.result.amount == Decimal("2272.73")  # 100 / 1.10 * 25


def test_eur_to_eur_is_identity() -> None:
    assert _conv().convert(Money.of("5.00", "EUR"), "EUR").result.amount == Decimal("5.00")


def test_unknown_currency_has_no_rate() -> None:
    with pytest.raises((RateUnavailableError, ValidationFailure)):
        _conv().convert(Money.of("1", "EUR"), "PLN")


def test_stale_rate_is_refused() -> None:
    with pytest.raises(RateUnavailableError):
        _conv(date(2026, 3, 20)).convert(Money.of("1", "EUR"), "USD")


def test_legacy_fixed_rate_currencies() -> None:
    assert get_currency("BGN") is not None
    assert get_currency("HRK") is not None


@pytest.mark.parametrize(
    ("locale", "expected_fragment"),
    [("en-US", "$"), ("en-GB", "£"), ("en-SG", "$"), ("de-CH", "CHF")],
)
def test_format_uses_home_symbols(locale: str, expected_fragment: str) -> None:
    cur = {"en-US": "USD", "en-GB": "GBP", "en-SG": "SGD", "de-CH": "CHF"}[locale]
    assert expected_fragment in format_money(Money.of("1234.5", cur), locale)


def test_format_czech_uses_space_grouping_and_comma() -> None:
    text = format_money(Money.of("1234.5", "CZK"), "cs")
    assert "," in text
    assert "1" in text and "234" in text


def test_foreign_currency_is_unambiguous_abroad() -> None:
    assert format_money(Money.of("5", "SGD"), "en-US").startswith("S$")
