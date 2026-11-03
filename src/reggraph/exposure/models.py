"""Exposure data shapes (Phase 3).

An *exposure position* says: ``holder`` is exposed to ``counterparty`` for ``amount`` in
``currency`` as at ``as_of``. Positions arrive in *batches*; a batch is immutable, carries a
content hash and the loading user, and every position keeps its batch id. That is the data
lineage: any figure can be traced back to the batch (and so to the source system) it came from.

Amounts are non-negative decimals sent as text (never floats, never exponents).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from reggraph.currency import get_currency, parse_money_amount
from reggraph.registry.models import Lei, Ref, Text60, Text200

MAX_POSITIONS_PER_BATCH = 1000


class ExposureType(StrEnum):
    LOAN = "loan"
    BOND = "bond"
    DERIVATIVE = "derivative"
    DEPOSIT = "deposit"
    REPO = "repo"
    GUARANTEE = "guarantee"
    EQUITY = "equity"
    OTHER = "other"


def _currency_code(value: str) -> str:
    cur = get_currency(value.strip().upper())
    if cur is None or not cur.active:
        raise ValueError("unsupported currency")
    return cur.code


class PositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    exposure_ref: Ref
    holder_lei: Lei
    counterparty_lei: Lei
    exposure_type: ExposureType
    currency: Annotated[str, AfterValidator(_currency_code)]
    amount: Decimal = Field(allow_inf_nan=False)
    as_of: date
    maturity_date: date | None = None
    source_ref: Text200 | None = None

    @model_validator(mode="before")
    @classmethod
    def _amount_plain(cls, data: object) -> object:
        """Parse the amount strictly *before* pydantic's own (more lenient) decimal parsing."""
        if isinstance(data, dict) and "amount" in data:
            code = str(data.get("currency", "")).strip().upper()
            if get_currency(code) is None:
                raise ValueError("unsupported currency")
            data = {**data, "amount": parse_money_amount(data["amount"], code)}
        return data

    @field_validator("amount")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("amount must not be negative")
        return value

    @model_validator(mode="after")
    def _check(self) -> PositionInput:
        if self.holder_lei == self.counterparty_lei:
            raise ValueError("an entity cannot be exposed to itself")
        if self.maturity_date is not None and self.maturity_date < self.as_of:
            raise ValueError("maturity_date must not precede as_of")
        return self


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_system: Text60
    description: Text200
    positions: tuple[PositionInput, ...] = Field(min_length=1, max_length=MAX_POSITIONS_PER_BATCH)

    @model_validator(mode="after")
    def _unique(self) -> BatchInput:
        seen: set[tuple[str, date]] = set()
        for pos in self.positions:
            key = (pos.exposure_ref, pos.as_of)
            if key in seen:
                raise ValueError("duplicate exposure_ref for the same as_of date in one batch")
            seen.add(key)
        return self

    def content_hash(self) -> str:
        body = json.dumps(
            [p.model_dump(mode="json") for p in self.positions],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)

    position_id: int
    batch_id: int
    position: PositionInput
    recorded_at: datetime


class Batch(BaseModel):
    model_config = ConfigDict(frozen=True)

    batch_id: int
    source_system: str
    description: str
    position_count: int
    content_hash: str
    loaded_by: str
    loaded_at: datetime
