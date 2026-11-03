"""Currency conversion through the euro.

Design (Phase 1):

* Market rates are quoted the way the ECB publishes its reference rates:
  "units of currency X per 1 EUR".
* Any pair is converted by triangulation through the euro: amount / rate_from * rate_to.
* Every conversion records the rate, its date and its source (audit and explainability).
* A rate older than ``max_age_days`` is refused (stale data is worse than no data).
* Legacy currencies (BGN, HRK) use their legal fixed euro rate and never go stale.
* No network access here. Rates come from a ``RateProvider`` so a licensed feed can be
  plugged in later (Phase 4) without touching this logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, localcontext
from typing import Protocol

from reggraph.core.clock import Clock
from reggraph.core.errors import RateUnavailableError, ValidationFailure
from reggraph.currency.codes import get_currency
from reggraph.currency.money import Money


@dataclass(frozen=True, slots=True)
class RateQuote:
    quote: str  # currency code
    per_eur: Decimal  # units of ``quote`` per 1 EUR
    as_of: date
    source: str


class RateProvider(Protocol):
    def per_eur(self, quote: str, on: date) -> RateQuote | None:
        """Latest quote dated on or before ``on``, or None."""
        ...


class InMemoryRates:
    """Simple provider for tests and local demos."""

    def __init__(self) -> None:
        self._quotes: dict[str, list[RateQuote]] = {}

    def add(self, quote: str, per_eur: Decimal, as_of: date, source: str) -> None:
        if per_eur <= 0:
            raise ValidationFailure("rate must be positive")
        self._quotes.setdefault(quote, []).append(RateQuote(quote, per_eur, as_of, source))

    def per_eur(self, quote: str, on: date) -> RateQuote | None:
        candidates = [q for q in self._quotes.get(quote, []) if q.as_of <= on]
        return max(candidates, key=lambda q: q.as_of) if candidates else None


@dataclass(frozen=True, slots=True)
class Conversion:
    source: Money
    result: Money
    rate: Decimal  # target units per 1 source unit, 10 decimal places
    rate_date: date  # the older of the two quote dates
    sources: tuple[str, ...]


class Converter:
    def __init__(self, provider: RateProvider, clock: Clock, max_age_days: int = 5) -> None:
        self._provider = provider
        self._clock = clock
        self._max_age_days = max_age_days

    def _quote(self, code: str, on: date) -> RateQuote:
        currency = get_currency(code)
        if currency is None:
            raise ValidationFailure(f"unknown currency: {code!r}")
        if currency.code == "EUR":
            return RateQuote("EUR", Decimal(1), on, "identity")
        if currency.legacy_eur_rate is not None:
            return RateQuote(
                currency.code, currency.legacy_eur_rate, on, "legal-fixed-conversion-rate"
            )
        quote = self._provider.per_eur(currency.code, on)
        if quote is None:
            raise RateUnavailableError(f"no rate for {currency.code} on or before {on}")
        if (on - quote.as_of).days > self._max_age_days:
            raise RateUnavailableError(f"rate for {currency.code} is stale ({quote.as_of})")
        return quote

    def convert(self, money: Money, to: str, on: date | None = None) -> Conversion:
        day = on or self._clock.now().date()
        target = get_currency(to)
        if target is None:
            raise ValidationFailure(f"unknown currency: {to!r}")
        if target.code == money.currency:
            return Conversion(money, money, Decimal(1), day, ("identity",))
        src = self._quote(money.currency, day)
        dst = self._quote(target.code, day)
        with localcontext() as ctx:
            ctx.prec = 40
            rate = dst.per_eur / src.per_eur
            result = Money(money.amount * rate, target.code)
            shown_rate = rate.quantize(Decimal("0.0000000001"))
        return Conversion(
            source=money,
            result=result,
            rate=shown_rate,
            rate_date=min(src.as_of, dst.as_of),
            sources=(src.source, dst.source),
        )
