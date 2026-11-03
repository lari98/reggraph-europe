"""The stress engine: a pure function from (scenario, snapshot, buffers) to a result.

====  ======================================================================================
E1    No I/O, no clock, no randomness: the same inputs give the same output, always.
E2    For one position the surviving fraction is the product of the fractions left by every
      shock that touches it, so the order of shocks never matters.
E3    Loss = amount x (1 - surviving fraction), rounded half-even to the currency's minor units
      per aggregated row; totals are exact sums of rounded values.
E4    Insurance: a holder's cover = sum of its active contracts' sums insured, each scaled by
      the surviving fraction of the insurer (0 for ``insurer_failure``). Recovery =
      min(gross loss, cover). This assumes the cover responds to the loss — a stated
      simplification (see docs/STRESS_LAB.md).
====  ======================================================================================
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from pydantic import BaseModel, ConfigDict

from reggraph.currency import Money
from reggraph.stress.models import (
    DefaultShock,
    FxShock,
    HaircutShock,
    InsurerFailureShock,
    ScenarioInput,
)

_ONE = Decimal(1)
_ZERO = Decimal(0)
_HUNDRED = Decimal(100)
_RATIO_Q = Decimal("0.0001")
TOP_CONTRIBUTORS = 20


class _M(BaseModel):
    model_config = ConfigDict(frozen=True)


class SnapRow(_M):
    holder_lei: str
    counterparty_lei: str
    counterparty_jurisdiction: str
    exposure_type: str
    currency: str  # the position's own currency (what an fx shock looks at)
    amount: Decimal  # in the run currency, aggregated


class SnapCover(_M):
    contract_ref: str
    version_no: int
    insurer_lei: str
    insured_lei: str
    amount: Decimal  # sum insured in the run currency


class Snapshot(_M):
    as_of: str
    currency: str
    max_batch: int
    rows: list[SnapRow]
    covers: list[SnapCover]
    group_members: dict[str, list[str]]  # defaulted LEI -> controlled group members
    lineage: dict[str, object]


class HolderResult(_M):
    holder_lei: str
    exposure: Decimal
    gross_loss: Decimal
    insurance_recovery: Decimal
    net_loss: Decimal
    net_loss_ratio: Decimal
    capital_buffer: Decimal | None
    breach: bool


class Contributor(_M):
    counterparty_lei: str
    gross_loss: Decimal


class Totals(_M):
    exposure: Decimal
    gross_loss: Decimal
    insurance_recovery: Decimal
    net_loss: Decimal
    breaches: int


class StressResult(_M):
    currency: str
    totals: Totals
    holders: list[HolderResult]
    top_contributors: list[Contributor]


def _survival_of_entities(scenario: ScenarioInput, snap: Snapshot) -> dict[str, Decimal]:
    survive: dict[str, Decimal] = {}
    for shock in scenario.shocks:
        if isinstance(shock, DefaultShock):
            keep = _ONE - shock.loss_given_default_percent / _HUNDRED
            targets = set(shock.leis)
            if shock.include_group:
                for lei in shock.leis:
                    targets.update(snap.group_members.get(lei, ()))
            for lei in targets:
                survive[lei] = survive.get(lei, _ONE) * keep
    return survive


def run(
    scenario: ScenarioInput, snap: Snapshot, buffers: dict[str, Decimal] | None = None
) -> StressResult:
    buffers = buffers or {}
    entity_survival = _survival_of_entities(scenario, snap)
    failed_insurers = {
        lei for s in scenario.shocks if isinstance(s, InsurerFailureShock) for lei in s.leis
    }
    exposure: dict[str, Decimal] = {}
    gross: dict[str, Decimal] = {}
    by_cpty: dict[str, Decimal] = {}
    for row in sorted(
        snap.rows,
        key=lambda r: (r.holder_lei, r.counterparty_lei, r.exposure_type, r.currency),
    ):
        survive = entity_survival.get(row.counterparty_lei, _ONE)
        for shock in scenario.shocks:
            if isinstance(shock, FxShock):
                if shock.currency == row.currency and shock.change_percent < 0:
                    survive *= _ONE + shock.change_percent / _HUNDRED
            elif isinstance(shock, HaircutShock):
                type_ok = (
                    shock.exposure_type is None or shock.exposure_type.value == row.exposure_type
                )
                jur_ok = (
                    shock.counterparty_jurisdiction is None
                    or shock.counterparty_jurisdiction == row.counterparty_jurisdiction
                )
                if type_ok and jur_ok:
                    survive *= _ONE - shock.percent / _HUNDRED
        loss = Money(row.amount * (_ONE - survive), snap.currency).amount
        exposure[row.holder_lei] = exposure.get(row.holder_lei, _ZERO) + row.amount
        gross[row.holder_lei] = gross.get(row.holder_lei, _ZERO) + loss
        by_cpty[row.counterparty_lei] = by_cpty.get(row.counterparty_lei, _ZERO) + loss

    cover: dict[str, Decimal] = {}
    if scenario.use_insurance:
        for c in snap.covers:
            keep = (
                _ZERO
                if c.insurer_lei in failed_insurers
                else entity_survival.get(c.insurer_lei, _ONE)
            )
            cover[c.insured_lei] = (
                cover.get(c.insured_lei, _ZERO) + Money(c.amount * keep, snap.currency).amount
            )

    holders: list[HolderResult] = []
    for lei in sorted(exposure):
        loss = gross[lei]
        recovery = min(loss, cover.get(lei, _ZERO))
        net = loss - recovery
        buf = buffers.get(lei)
        ratio = (
            (net / exposure[lei]).quantize(_RATIO_Q, ROUND_HALF_EVEN) if exposure[lei] else _ZERO
        )
        holders.append(
            HolderResult(
                holder_lei=lei,
                exposure=exposure[lei],
                gross_loss=loss,
                insurance_recovery=recovery,
                net_loss=net,
                net_loss_ratio=ratio,
                capital_buffer=buf,
                breach=buf is not None and net > buf,
            )
        )
    holders.sort(key=lambda h: (-h.net_loss, h.holder_lei))
    contributors = sorted(
        (Contributor(counterparty_lei=k, gross_loss=v) for k, v in by_cpty.items() if v > 0),
        key=lambda c: (-c.gross_loss, c.counterparty_lei),
    )[:TOP_CONTRIBUTORS]
    totals = Totals(
        exposure=sum((h.exposure for h in holders), _ZERO),
        gross_loss=sum((h.gross_loss for h in holders), _ZERO),
        insurance_recovery=sum((h.insurance_recovery for h in holders), _ZERO),
        net_loss=sum((h.net_loss for h in holders), _ZERO),
        breaches=sum(1 for h in holders if h.breach),
    )
    return StressResult(
        currency=snap.currency, totals=totals, holders=holders, top_contributors=contributors
    )
