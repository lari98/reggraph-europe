"""Insurance contract data shapes (Phase 3).

A contract links an *insurer* (or reinsurer) to the *insured* party for a sum insured over an
inclusive period (``start_date`` .. ``end_date``; ``None`` end = open-ended). A reinsurance
contract uses ``reinsures_ref`` to name the contract it covers. Contracts are versioned like
registry entities: a change writes a new version and history is never edited.
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
from reggraph.registry.models import Lei, Ref


class InsuranceLine(StrEnum):
    LIFE = "life"
    NON_LIFE = "non_life"
    HEALTH = "health"
    CREDIT_AND_SURETY = "credit_and_surety"
    REINSURANCE = "reinsurance"


def _currency_code(value: str) -> str:
    cur = get_currency(value.strip().upper())
    if cur is None or not cur.active:
        raise ValueError("unsupported currency")
    return cur.code


class ContractInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_ref: Ref
    insurer_lei: Lei
    insured_lei: Lei
    line: InsuranceLine
    currency: Annotated[str, AfterValidator(_currency_code)]
    sum_insured: Decimal = Field(allow_inf_nan=False)
    start_date: date
    end_date: date | None = None
    reinsures_ref: Ref | None = None

    @model_validator(mode="before")
    @classmethod
    def _amount_plain(cls, data: object) -> object:
        if isinstance(data, dict) and "sum_insured" in data:
            code = str(data.get("currency", "")).strip().upper()
            if get_currency(code) is None:
                raise ValueError("unsupported currency")
            data = {**data, "sum_insured": parse_money_amount(data["sum_insured"], code)}
        return data

    @field_validator("sum_insured")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("sum_insured must be greater than zero")
        return value

    @model_validator(mode="after")
    def _check(self) -> ContractInput:
        if self.insurer_lei == self.insured_lei:
            raise ValueError("insurer and insured must be different entities")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not precede start_date")
        if self.reinsures_ref is not None and self.line is not InsuranceLine.REINSURANCE:
            raise ValueError("only a reinsurance contract may name reinsures_ref")
        if self.reinsures_ref == self.contract_ref:
            raise ValueError("a contract cannot reinsure itself")
        return self

    def active_on(self, day: date) -> bool:
        return self.start_date <= day and (self.end_date is None or day <= self.end_date)

    def content_hash(self) -> str:
        body = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ContractVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_ref: str
    version_no: int
    contract: ContractInput
    content_hash: str
    change_summary: str
    changed_by: str
    changed_at: datetime
