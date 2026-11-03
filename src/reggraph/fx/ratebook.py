"""Stores each tenant's exchange rates (EUR-based, ECB style: units per 1 EUR).

Rules
-----
====  ================================================================================
F1    Rates are insert-only per (tenant, currency, date): history is never rewritten.
F2    Only currencies that are active and not EUR can be quoted; legacy ones are fixed by law.
F3    A rate must be positive, below 10 million and have at most 10 decimals.
F4    The rate date must not be in the future (no look-ahead data).
F5    Writing needs the ``fx:write`` permission; the tenant comes from the principal.
====  ================================================================================

The source label (for example ``"ECB euro foreign exchange reference rates, manual import"``)
is stored with every rate so a conversion can always be traced back.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Final

from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, ValidationFailure
from reggraph.currency.codes import get_currency
from reggraph.currency.rates import RateQuote
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.security.validation import clean_text
from reggraph.storage.sqlite import Database

_MAX_RATE = Decimal("10000000")
_RATE_RE: Final = re.compile(r"[0-9]{1,7}(\.[0-9]{1,10})?")
_MAX_QUOTES = 40


class TenantRates:
    """A ``RateProvider`` bound to one tenant."""

    def __init__(self, db: Database, tenant_id: str) -> None:
        self._db = db
        self._tenant = tenant_id

    def per_eur(self, quote: str, on: date) -> RateQuote | None:
        rows = self._db.query(
            "SELECT quote, per_eur, as_of, source FROM fx_rates WHERE tenant_id = ? AND quote = ?"
            " AND as_of <= ? ORDER BY as_of DESC LIMIT 1",
            (self._tenant, quote, on.isoformat()),
        )
        if not rows:
            return None
        row = rows[0]
        return RateQuote(
            row["quote"], Decimal(row["per_eur"]), date.fromisoformat(row["as_of"]), row["source"]
        )


class RateBook:
    def __init__(self, db: Database, audit: AuditLog, clock: Clock) -> None:
        self._db = db
        self._audit = audit
        self._clock = clock

    def provider_for(self, tenant_id: str) -> TenantRates:
        return TenantRates(self._db, tenant_id)

    def put(self, who: Principal, as_of: date, source: str, rates: dict[str, str]) -> int:
        """Record a day's rates. Returns how many were stored. All-or-nothing."""
        who.require(Permission.FX_WRITE)
        label = clean_text(source, max_len=100)
        if not rates or len(rates) > _MAX_QUOTES:
            raise ValidationFailure("between 1 and 40 rates are required")
        if as_of > self._clock.now().date():
            raise ValidationFailure("rate date is in the future")
        parsed: dict[str, Decimal] = {}
        for code, text in rates.items():
            currency = get_currency(code)
            if currency is None or currency.code == "EUR" or not currency.active:
                raise ValidationFailure(f"currency {code!r} cannot be quoted")
            if not _RATE_RE.fullmatch(text):  # plain ASCII digits only: no exponent, NaN, spaces
                raise ValidationFailure("rate must be a plain decimal number")
            value = Decimal(text)
            if value <= 0 or value >= _MAX_RATE:
                raise ValidationFailure("rate out of range")
            parsed[currency.code] = value
        with self._db.transaction():
            for code, value in parsed.items():
                exists = self._db.query(
                    "SELECT 1 FROM fx_rates WHERE tenant_id = ? AND quote = ? AND as_of = ?",
                    (who.tenant_id, code, as_of.isoformat()),
                )
                if exists:
                    raise ConflictError(f"rate for {code} on {as_of} already exists")
                self._db.execute(
                    "INSERT INTO fx_rates (tenant_id, quote, per_eur, as_of, source)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (who.tenant_id, code, format(value, "f"), as_of.isoformat(), label),
                )
            self._audit.append(
                who.tenant_id,
                f"user:{who.user_id}",
                "fx.rates_stored",
                as_of.isoformat(),
                {"currencies": sorted(parsed), "source": label},
            )
        return len(parsed)
