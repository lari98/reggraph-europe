"""Locale-aware money formatting (no third-party dependency).

This is a compact table of the number conventions of the supported locales. It is an
approximation of CLDR data and is validated against CLDR in Phase 5 (see docs/I18N_AND_CURRENCY.md).

Locale   decimal  group   symbol  example (1234.56 EUR)
-------  -------  ------  ------  ----------------------
en-*     .        ,       before  €1,234.56
de       ,        .       after   1.234,56 €
de-CH    .        ’       before  € 1’234.56   (CHF 1’234.56 for francs)
fr       ,        U+202F  after   1 234,56 €
pl/cs/.. ,        U+00A0  after   1 234,56 €
nl       ,        .       before  € 1.234,56
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from reggraph.core.errors import ValidationFailure
from reggraph.currency.codes import get_currency
from reggraph.currency.money import Money

NBSP: Final = " "
NNBSP: Final = " "


@dataclass(frozen=True, slots=True)
class _Style:
    decimal: str
    group: str
    symbol_before: bool
    space: str  # between symbol and number ("" or NBSP)
    min_group: int = 1  # 2 = do not group 4-digit numbers (es, pt, pl, bg)


_EN = _Style(".", ",", True, "")
_SUFFIX_NBSP = _Style(",", NBSP, False, NBSP)
_DOT_SUFFIX = _Style(",", ".", False, NBSP)
_SWISS = _Style(".", "’", True, NBSP)

_STYLES: Final[dict[str, _Style]] = {
    "en": _EN,
    "ga": _EN,
    "mt": _EN,
    "zh": _EN,
    "ms": _EN,
    "ta": _EN,
    "de": _DOT_SUFFIX,
    "it": _DOT_SUFFIX,
    "ro": _DOT_SUFFIX,
    "hr": _DOT_SUFFIX,
    "sl": _DOT_SUFFIX,
    "da": _DOT_SUFFIX,
    "el": _DOT_SUFFIX,
    "es": _Style(",", ".", False, NBSP, 2),
    "nl": _Style(",", ".", True, NBSP),
    "fr": _Style(",", NNBSP, False, NBSP),
    "pt": _Style(",", NBSP, False, NBSP, 2),
    "pl": _Style(",", NBSP, False, NBSP, 2),
    "bg": _Style(",", NBSP, False, NBSP, 2),
    "cs": _SUFFIX_NBSP,
    "sk": _SUFFIX_NBSP,
    "hu": _SUFFIX_NBSP,
    "lt": _SUFFIX_NBSP,
    "lv": _SUFFIX_NBSP,
    "et": _SUFFIX_NBSP,
    "fi": _SUFFIX_NBSP,
    "sv": _SUFFIX_NBSP,
    "rm": _SWISS,
    "de-CH": _SWISS,
    "it-CH": _SWISS,
    "fr-CH": _Style(",", NNBSP, False, NBSP),
}

# The currency's home symbol is used inside its own country; abroad the longer symbol avoids
# confusion between the many "dollar" currencies.
_HOME_SYMBOL: Final[dict[tuple[str, str], str]] = {
    ("en-SG", "SGD"): "$",
    ("zh-SG", "SGD"): "$",
    ("ms-SG", "SGD"): "$",
    ("ta-SG", "SGD"): "$",
}
_ABROAD_SYMBOL: Final[dict[tuple[str, str], str]] = {
    ("en-SG", "USD"): "US$",
    ("zh-SG", "USD"): "US$",
    ("ms-SG", "USD"): "US$",
    ("ta-SG", "USD"): "US$",
    ("en-GB", "USD"): "US$",
}


def _style_for(locale: str) -> _Style:
    if locale in _STYLES:
        return _STYLES[locale]
    return _STYLES.get(locale.split("-")[0], _EN)


def _group_digits(digits: str, style: _Style) -> str:
    if len(digits) <= 3 or (style.min_group == 2 and len(digits) == 4):
        return digits
    parts: list[str] = []
    while digits:
        parts.append(digits[-3:])
        digits = digits[:-3]
    return style.group.join(reversed(parts))


def format_money(money: Money, locale: str = "en") -> str:
    """Format ``money`` for ``locale`` (a tag such as ``de-CH``). Unknown locales use English."""
    currency = get_currency(money.currency)
    if currency is None:  # pragma: no cover - Money already validated the code
        raise ValidationFailure("unknown currency")
    style = _style_for(locale)
    symbol = _HOME_SYMBOL.get((locale, currency.code)) or _ABROAD_SYMBOL.get(
        (locale, currency.code), currency.symbol
    )
    amount = money.amount
    negative = amount < 0
    text = format(abs(amount), "f")
    whole, _, frac = text.partition(".")
    number = _group_digits(whole, style)
    if currency.minor_units > 0:
        number += style.decimal + frac.ljust(currency.minor_units, "0")
    sign = "-" if negative and amount != Decimal(0) else ""
    if style.symbol_before:
        return f"{sign}{symbol}{style.space}{number}"
    return f"{sign}{number}{style.space}{symbol}"
