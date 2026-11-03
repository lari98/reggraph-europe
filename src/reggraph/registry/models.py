"""Registry data shapes. Input models reject unknown fields and unsafe text.

Date convention (important): every period is **inclusive** at both ends. ``valid_from`` /
``valid_until`` for licences and ``effective_from`` / ``effective_until`` for ownership mean
"applies on that day and on every day up to and including that day". ``None`` means open-ended.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from reggraph.currency import get_currency
from reggraph.jurisdictions import get_jurisdiction
from reggraph.registry.lei import validate_lei
from reggraph.regulation.models import ConsolidationBasis, EntityType
from reggraph.security.validation import clean_text


def _text(max_len: int) -> AfterValidator:
    return AfterValidator(lambda v: clean_text(v, max_len=max_len))


_PERCENT_RE = re.compile(r"[0-9]{1,3}(\.[0-9]{1,2})?")

Text60 = Annotated[str, _text(60)]
Text100 = Annotated[str, _text(100)]
Text200 = Annotated[str, _text(200)]
Lei = Annotated[str, AfterValidator(validate_lei)]


class LicenceType(StrEnum):
    BANKING = "banking"
    INSURANCE = "insurance"
    REINSURANCE = "reinsurance"
    INVESTMENT_SERVICES = "investment_services"
    FUND_MANAGEMENT = "fund_management"
    PAYMENT_INSTITUTION = "payment_institution"
    E_MONEY = "e_money"


class EntityStatus(StrEnum):
    ACTIVE = "active"
    DISSOLVED = "dissolved"


class OwnershipKind(StrEnum):
    SUBSIDIARY = "subsidiary"
    PARTICIPATION = "participation"
    BRANCH = "branch"


def _country(value: str) -> str:
    jur = get_jurisdiction(value.strip().upper())
    if jur is None:
        raise ValueError("unknown jurisdiction")
    return jur.code  # normalises the UK alias to GB


class Licence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    licence_type: LicenceType
    authority: Text100
    jurisdiction: Annotated[str, AfterValidator(_country)]
    reference: Text100 | None = None
    valid_from: date
    valid_until: date | None = None

    @model_validator(mode="after")
    def _order(self) -> Licence:
        if self.valid_until is not None and self.valid_until < self.valid_from:
            raise ValueError("valid_until must not precede valid_from")
        return self

    def valid_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_until is None or day <= self.valid_until)


class EntityInput(BaseModel):
    """A bank, insurer or corporate as submitted. The LEI is the key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lei: Lei
    legal_name: Text200
    entity_type: EntityType
    jurisdiction: Annotated[str, AfterValidator(_country)]
    reporting_currency: str | None = None
    reporting_basis: ConsolidationBasis = ConsolidationBasis.INDIVIDUAL
    registration_number: Text60 | None = None
    status: EntityStatus = EntityStatus.ACTIVE
    licences: tuple[Licence, ...] = Field(default=(), max_length=30)

    @field_validator("reporting_currency")
    @classmethod
    def _currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cur = get_currency(value.strip().upper())
        if cur is None or not cur.active:
            raise ValueError("unsupported reporting currency")
        return cur.code

    @property
    def effective_currency(self) -> str:
        """Reporting currency, defaulting to the home jurisdiction's own currency."""
        if self.reporting_currency:
            return self.reporting_currency
        jur = get_jurisdiction(self.jurisdiction)
        return jur.currency if jur else "EUR"

    def content_hash(self) -> str:
        body = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class EntityVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    lei: str
    version_no: int
    entity: EntityInput
    content_hash: str
    change_summary: str
    changed_by: str
    changed_at: datetime


class OwnershipInput(BaseModel):
    """``parent`` holds ``percent`` of ``child`` (or owns it as a branch) for a period."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parent_lei: Lei
    child_lei: Lei
    percent: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2, allow_inf_nan=False)
    kind: OwnershipKind = OwnershipKind.SUBSIDIARY
    effective_from: date
    effective_until: date | None = None

    @field_validator("percent", mode="before")
    @classmethod
    def _plain_percent(cls, value: object) -> object:
        """Only plain decimal text or an integer: no exponent, no float, no spaces."""
        if isinstance(value, bool | float):
            raise ValueError("percent must be a decimal string or an integer")
        if isinstance(value, str) and not _PERCENT_RE.fullmatch(value):
            raise ValueError("percent must look like 12 or 12.5")
        return value

    @model_validator(mode="after")
    def _check(self) -> OwnershipInput:
        if self.parent_lei == self.child_lei:
            raise ValueError("an entity cannot own itself")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not precede effective_from")
        if self.kind is OwnershipKind.BRANCH and self.percent != 100:
            raise ValueError("a branch is wholly part of its parent (percent must be 100)")
        return self


class OwnershipLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    link_id: int
    link: OwnershipInput
    created_by: str
    created_at: datetime


Reason = Literal["individual", "consolidated"]
