"""Request bodies. ``extra="forbid"`` rejects unknown fields (no mass assignment)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from reggraph.regulation.models import RuleInput
from reggraph.security.validation import clean_text

_Note = Annotated[str, AfterValidator(lambda v: clean_text(v, max_len=500, allow_empty=True))]
_Summary = Annotated[str, AfterValidator(lambda v: clean_text(v, max_len=500))]
_Source = Annotated[str, AfterValidator(lambda v: clean_text(v, max_len=300))]
_Label = Annotated[str, AfterValidator(lambda v: clean_text(v, max_len=100))]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposeRequest(_Strict):
    rule: RuleInput
    change_summary: _Summary


class IngestRequest(_Strict):
    rule: RuleInput
    source_ref: _Source


class DecisionRequest(_Strict):
    note: _Note = ""


class FxRatesRequest(_Strict):
    as_of: date
    source: _Label
    rates: dict[str, str] = Field(min_length=1, max_length=40)


class ConvertRequest(_Strict):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    amount: str = Field(max_length=40)
    from_currency: str = Field(alias="from", pattern=r"^[A-Za-z]{3}$")
    to: str = Field(pattern=r"^[A-Za-z]{3}$")
    on: date | None = None
