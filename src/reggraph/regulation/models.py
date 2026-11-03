"""Data model of a regulatory rule record.

Fields follow the "rule record" in the Pitch Pack (section 6): authoritative citation,
version and dates, jurisdiction and legal scope, entity type, consolidated-versus-individual
applicability, conditions, numerical requirements, exemptions, control owner, mapped model,
impacted data, required approval, change history and sign-off.

Everything is validated on the way in (``extra="forbid"`` blocks mass-assignment of fields like
``status`` or ``approved_by``) and re-validated when read back from storage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from reggraph.i18n.locales import is_supported
from reggraph.jurisdictions import expand_scope
from reggraph.jurisdictions.registry import EU_TOKEN, get_jurisdiction
from reggraph.security.validation import clean_text, ensure_rule_key


def _text(max_len: int, *, multiline: bool = False) -> AfterValidator:
    return AfterValidator(lambda v: clean_text(v, max_len=max_len, multiline=multiline))


Text100 = Annotated[str, _text(100)]
Text200 = Annotated[str, _text(200)]
Text300 = Annotated[str, _text(300)]
Text500 = Annotated[str, _text(500, multiline=True)]


class EntityType(StrEnum):
    BANK = "bank"
    INSURER = "insurer"
    REINSURER = "reinsurer"
    INVESTMENT_FIRM = "investment_firm"
    FUND_MANAGER = "fund_manager"
    CORPORATE = "corporate"
    HOLDING = "holding"


class ConsolidationBasis(StrEnum):
    CONSOLIDATED = "consolidated"
    INDIVIDUAL = "individual"
    BOTH = "both"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class Comparator(StrEnum):
    GTE = ">="
    LTE = "<="
    GT = ">"
    LT = "<"
    EQ = "="


class NumericRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Text100
    comparator: Comparator
    value: Decimal = Field(max_digits=20, decimal_places=8, allow_inf_nan=False)
    unit: Text100


class RuleInput(BaseModel):
    """What a person (or the ingestion service) submits. Never contains status or approvals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_key: str
    title: Text200
    title_i18n: dict[str, Text200] = Field(default_factory=dict, max_length=40)
    source_language: str = "en"
    citation: Text300
    source_url: str | None = None
    jurisdictions: tuple[str, ...] = Field(min_length=1, max_length=40)
    entity_types: tuple[EntityType, ...] = Field(min_length=1, max_length=7)
    consolidation: ConsolidationBasis
    publication_date: date
    effective_date: date
    application_date: date
    transition_end: date | None = None
    conditions: tuple[Text500, ...] = Field(default=(), max_length=20)
    exemptions: tuple[Text500, ...] = Field(default=(), max_length=20)
    numeric_requirements: tuple[NumericRequirement, ...] = Field(default=(), max_length=30)
    control_owner: Text100
    mapped_model: Text100 | None = None
    impacted_data: tuple[Text100, ...] = Field(default=(), max_length=20)
    required_approval: Literal["compliance_officer"] = "compliance_officer"

    @field_validator("rule_key")
    @classmethod
    def _check_key(cls, value: str) -> str:
        return ensure_rule_key(value)

    @field_validator("source_language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        if not is_supported(value):
            raise ValueError("unsupported language")
        return value

    @field_validator("title_i18n")
    @classmethod
    def _check_i18n_keys(cls, value: dict[str, str]) -> dict[str, str]:
        for tag in value:
            if not is_supported(tag):
                raise ValueError("unsupported language in title_i18n")
        return value

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = clean_text(value, max_len=500)
        parts = urlsplit(text)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("source_url must be an https URL without credentials")
        return text

    @field_validator("jurisdictions")
    @classmethod
    def _check_jurisdictions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: list[str] = []
        for token in value:
            upper = token.strip().upper()
            if upper != EU_TOKEN:
                jur = get_jurisdiction(upper)
                if jur is None:
                    raise ValueError("unknown jurisdiction")
                upper = jur.code  # normalises the UK alias to GB
            if upper not in seen:
                seen.append(upper)
        return tuple(seen)

    @model_validator(mode="after")
    def _check_dates(self) -> RuleInput:
        if not self.publication_date <= self.effective_date <= self.application_date:
            raise ValueError("dates must satisfy publication <= effective <= application")
        if self.transition_end is not None and self.transition_end < self.application_date:
            raise ValueError("transition_end must not precede application_date")
        return self

    def scope_codes(self) -> frozenset[str]:
        return expand_scope(self.jurisdictions)

    def content_hash(self) -> str:
        body = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class RuleVersion(BaseModel):
    """One immutable version of a rule plus its review state."""

    model_config = ConfigDict(frozen=True)

    rule_key: str
    version_no: int
    status: RuleStatus
    rule: RuleInput
    content_hash: str
    change_summary: str
    source: Literal["manual", "ingestion"]
    proposed_by: str
    proposed_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


class ReviewTask(BaseModel):
    """Work item created when a rule changes: the control owner must review it."""

    model_config = ConfigDict(frozen=True)

    task_id: int
    rule_key: str
    version_no: int
    owner: str
    status: Literal["open", "done"]
    created_at: datetime


__all__ = [
    "Comparator",
    "ConsolidationBasis",
    "EntityType",
    "NumericRequirement",
    "ReviewTask",
    "RuleInput",
    "RuleStatus",
    "RuleVersion",
]
