"""Stress scenario shapes (Phase 4).

A scenario is a named, versioned list of *shocks*. Percentages are plain decimal text with at
most two decimals (no exponent, no float). Four kinds of shock:

``fx``               a currency moves by ``change_percent`` against the euro; only a fall hurts a
                     holder of positions in that currency (a rise is ignored, not a gain).
``default``          the named entities fail and lose ``loss_given_default_percent`` of what they
                     owe; with ``include_group`` their controlled group members fail too.
``haircut``          a flat write-down of positions matching an exposure type and / or a
                     counterparty jurisdiction.
``insurer_failure``  the named insurers pay nothing (their cover disappears).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from reggraph.currency import get_currency
from reggraph.exposure.models import ExposureType
from reggraph.registry.models import Lei, Ref, Text100, Text200, _country

MAX_SHOCKS = 50
_PCT_RE = re.compile(r"-?[0-9]{1,3}(\.[0-9]{1,2})?")


def _plain_pct(value: object) -> object:
    if isinstance(value, bool | float):
        raise ValueError("percent must be a decimal string or an integer")
    if isinstance(value, str) and not _PCT_RE.fullmatch(value):
        raise ValueError("percent must look like 12 or -12.5")
    return value


def _currency_code(value: str) -> str:
    cur = get_currency(value.strip().upper())
    if cur is None or not cur.active:
        raise ValueError("unsupported currency")
    return cur.code


class _Shock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FxShock(_Shock):
    kind: Literal["fx"]
    currency: Annotated[str, AfterValidator(_currency_code)]
    change_percent: Decimal = Field(ge=-100, le=100, max_digits=6, decimal_places=2)

    _pct = field_validator("change_percent", mode="before")(_plain_pct)


class DefaultShock(_Shock):
    kind: Literal["default"]
    leis: tuple[Lei, ...] = Field(min_length=1, max_length=MAX_SHOCKS)
    loss_given_default_percent: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    include_group: bool = True

    _pct = field_validator("loss_given_default_percent", mode="before")(_plain_pct)


class HaircutShock(_Shock):
    kind: Literal["haircut"]
    percent: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)
    exposure_type: ExposureType | None = None
    counterparty_jurisdiction: Annotated[str, AfterValidator(_country)] | None = None

    _pct = field_validator("percent", mode="before")(_plain_pct)

    @model_validator(mode="after")
    def _has_filter(self) -> HaircutShock:
        if self.exposure_type is None and self.counterparty_jurisdiction is None:
            raise ValueError("a haircut needs an exposure_type or a counterparty_jurisdiction")
        return self


class InsurerFailureShock(_Shock):
    kind: Literal["insurer_failure"]
    leis: tuple[Lei, ...] = Field(min_length=1, max_length=MAX_SHOCKS)


Shock = Annotated[
    FxShock | DefaultShock | HaircutShock | InsurerFailureShock, Field(discriminator="kind")
]


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_key: Ref
    title: Text100
    description: Text200 = ""
    shocks: tuple[Shock, ...] = Field(min_length=1, max_length=MAX_SHOCKS)
    use_insurance: bool = True

    def content_hash(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json")).encode()).hexdigest()

    def named_leis(self) -> set[str]:
        out: set[str] = set()
        for s in self.shocks:
            if isinstance(s, DefaultShock | InsurerFailureShock):
                out.update(s.leis)
        return out


class ScenarioVersion(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_key: str
    version_no: int
    scenario: ScenarioInput
    content_hash: str
    change_summary: str
    changed_by: str
    changed_at: datetime


def canonical(obj: object) -> str:
    """The one JSON form used for every hash: sorted keys, no spaces, UTF-8 characters kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
