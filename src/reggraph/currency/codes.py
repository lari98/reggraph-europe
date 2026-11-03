"""Currency reference data (ISO 4217).

Why this exists: not every EU country uses the euro (Czechia, Denmark, Hungary, Poland,
Romania and Sweden do not), and the platform also covers CHF, USD, GBP and SGD.

Legacy currencies (BGN, HRK) are kept so historical records can still be read. They were
replaced by the euro at an irrevocable fixed rate, so they convert without a market rate.

Code  Name              Minor units  Status
----  ----------------  -----------  -------------------------------------------
EUR   Euro              2            active
CZK   Czech koruna      2            active
DKK   Danish krone      2            active
HUF   Hungarian forint  2            active (ISO 4217 says 2, cash rounds to 0)
PLN   Polish zloty      2            active
RON   Romanian leu      2            active
SEK   Swedish krona     2            active
CHF   Swiss franc       2            active
USD   US dollar         2            active
GBP   Pound sterling    2            active
SGD   Singapore dollar  2            active
BGN   Bulgarian lev     2            legacy, fixed 1 EUR = 1.95583 BGN (to 2025-12-31)
HRK   Croatian kuna     2            legacy, fixed 1 EUR = 7.53450 HRK (to 2022-12-31)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final


@dataclass(frozen=True, slots=True)
class Currency:
    code: str
    numeric: str
    name: str
    minor_units: int
    symbol: str
    active: bool = True
    legacy_eur_rate: Decimal | None = None  # units of this currency per 1 EUR, fixed by law
    retired_on: date | None = None


CURRENCIES: Final[dict[str, Currency]] = {
    c.code: c
    for c in (
        Currency("EUR", "978", "Euro", 2, "€"),
        Currency("CZK", "203", "Czech koruna", 2, "Kč"),
        Currency("DKK", "208", "Danish krone", 2, "kr."),
        Currency("HUF", "348", "Hungarian forint", 2, "Ft"),
        Currency("PLN", "985", "Polish zloty", 2, "zł"),
        Currency("RON", "946", "Romanian leu", 2, "lei"),
        Currency("SEK", "752", "Swedish krona", 2, "kr"),
        Currency("CHF", "756", "Swiss franc", 2, "CHF"),
        Currency("USD", "840", "US dollar", 2, "$"),
        Currency("GBP", "826", "Pound sterling", 2, "£"),
        Currency("SGD", "702", "Singapore dollar", 2, "S$"),
        Currency(
            "BGN", "975", "Bulgarian lev", 2, "лв.", False, Decimal("1.95583"), date(2026, 1, 1)
        ),
        Currency(
            "HRK", "191", "Croatian kuna", 2, "kn", False, Decimal("7.53450"), date(2023, 1, 1)
        ),
    )
}


def get_currency(code: str) -> Currency | None:
    """Look up by ISO 4217 code (case-insensitive)."""
    return CURRENCIES.get(code.strip().upper())
