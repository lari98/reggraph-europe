"""Exact money arithmetic.

Rules (enforced here, tested in tests/unit/test_money.py):

====  =========================================================================
Rule  Reason
====  =========================================================================
M1    Amounts are ``Decimal``. ``float`` is rejected (binary rounding errors).
M2    Amounts are parsed from plain decimal text only. No exponents ("1e999").
M3    Amounts are limited to 15 integer digits (denial-of-service guard).
M4    Rounding is banker's rounding (ROUND_HALF_EVEN) to the currency's minor units.
M5    Adding or subtracting different currencies raises. Convert first.
====  =========================================================================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

from reggraph.core.errors import ValidationFailure
from reggraph.currency.codes import Currency, get_currency

_AMOUNT_RE: Final = re.compile(r"^[+-]?\d{1,15}(\.\d{1,12})?$")
_MAX_INPUT_LEN: Final = 40
_MAX_ABS: Final = Decimal("1e15")


def parse_amount(value: str | int | Decimal) -> Decimal:
    """Parse an amount from text, int or Decimal. Rejects float, bool and exponent notation."""
    if isinstance(value, bool | float):
        raise ValidationFailure("amount must not be float or bool")
    if isinstance(value, int):
        value = str(value)
    if isinstance(value, Decimal):
        # Already a Decimal (internal arithmetic): only guard finiteness and size (M3).
        if not value.is_finite() or abs(value) >= _MAX_ABS:
            raise ValidationFailure("amount must be finite and below 1e15")
        return value
    if not isinstance(value, str) or len(value) > _MAX_INPUT_LEN or not _AMOUNT_RE.match(value):
        raise ValidationFailure("amount is not a plain decimal number")
    return Decimal(value)


def _currency_or_fail(code: str) -> Currency:
    currency = get_currency(code)
    if currency is None:
        raise ValidationFailure(f"unknown currency: {code!r}")
    return currency


@dataclass(frozen=True, slots=True)
class Money:
    """An amount in one currency, always rounded to that currency's minor units."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        currency = _currency_or_fail(self.currency)
        exponent = Decimal(1).scaleb(-currency.minor_units)
        with localcontext() as ctx:
            ctx.prec = 40
            rounded = parse_amount(self.amount).quantize(exponent, rounding=ROUND_HALF_EVEN)
        object.__setattr__(self, "amount", rounded)
        object.__setattr__(self, "currency", currency.code)

    @classmethod
    def of(cls, amount: str | int | Decimal, currency: str) -> Money:
        return cls(parse_amount(amount), currency)

    def _same(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValidationFailure("currency mismatch; convert first")

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def scale(self, factor: str | int | Decimal) -> Money:
        """Multiply by a plain decimal factor (for example a 25 percent shock = "0.25")."""
        with localcontext() as ctx:
            ctx.prec = 40
            return Money(self.amount * parse_amount(factor), self.currency)

    def is_zero(self) -> bool:
        return self.amount == 0

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"
