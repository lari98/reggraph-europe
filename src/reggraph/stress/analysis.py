"""Advanced stress analysis built on the deterministic engine (0.2.0).

Three questions a single run cannot answer:

* **Severity ladder** - what happens if the scenario is 0.5x, 1.5x, 2x ... as bad?
* **Reverse stress test** - how many times worse than the scenario must it get before each
  institution's capital buffer is used up? (Regulators ask this: "what breaks you?")
* **Contagion cascade** - an institution that breaches its buffer may itself fail and hurt the
  institutions that lent to it. The cascade repeats the run until nobody new fails.

Everything here is a pure function of the frozen inputs of a stored run, so results are
reproducible and nothing is written. Shock scaling keeps every shock inside its legal range.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from pydantic import BaseModel, ConfigDict

from reggraph.stress import engine
from reggraph.stress.models import (
    MAX_SHOCKS,
    DefaultShock,
    FxShock,
    HaircutShock,
    ScenarioInput,
    Shock,
)

LADDER: Final = (Decimal("0.5"), Decimal(1), Decimal("1.5"), Decimal(2), Decimal(3), Decimal(4))
# Geometric search grid for the reverse test: 0.1x ... 10x of the scenario in 41 steps.
_GRID: Final = tuple(Decimal(10) ** (Decimal(i) / Decimal(20) - 1) for i in range(41))
MAX_CASCADE_ROUNDS: Final = 5
CASCADE_LGD: Final = Decimal(40)  # a failed institution passes on 40% of what it owed
_TWO: Final = Decimal("0.01")
_ONE_HUNDRED: Final = Decimal(100)


class _M(BaseModel):
    model_config = ConfigDict(frozen=True)


class LadderStep(_M):
    multiplier: Decimal
    gross_loss: Decimal
    insurance_recovery: Decimal
    net_loss: Decimal
    breaches: int
    worst_holder: str | None


class BreakingPoint(_M):
    holder_lei: str
    capital_buffer: Decimal
    net_loss: Decimal  # at the scenario as written (1x)
    multiplier: Decimal | None  # None: survives even 10x
    already_breached: bool


class CascadeRound(_M):
    round: int
    newly_failed: list[str]
    gross_loss: Decimal
    net_loss: Decimal
    breaches: int


class Analysis(_M):
    currency: str
    ladder: list[LadderStep]
    reverse: list[BreakingPoint]
    system_multiplier: Decimal | None  # first multiplier at which any institution breaches
    cascade: list[CascadeRound]


def _scale(shock: Shock, m: Decimal) -> Shock:
    """One shock made ``m`` times as severe, never beyond what the model accepts."""
    if isinstance(shock, DefaultShock):
        lgd = min(_ONE_HUNDRED, (shock.loss_given_default_percent * m).quantize(_TWO))
        return shock.model_copy(update={"loss_given_default_percent": max(_TWO, lgd)})
    if isinstance(shock, HaircutShock):
        pct = min(_ONE_HUNDRED, (shock.percent * m).quantize(_TWO))
        return shock.model_copy(update={"percent": max(_TWO, pct)})
    if isinstance(shock, FxShock):
        chg = max(-_ONE_HUNDRED, (shock.change_percent * m).quantize(_TWO))
        return shock.model_copy(update={"change_percent": chg})
    return shock  # an insurer failure is all-or-nothing


def scaled(scenario: ScenarioInput, m: Decimal) -> ScenarioInput:
    """The scenario ``m`` times as severe (frozen models: build a copy)."""
    return scenario.model_copy(update={"shocks": tuple(_scale(s, m) for s in scenario.shocks)})


def _step(m: Decimal, res: engine.StressResult) -> LadderStep:
    worst = max(res.holders, key=lambda h: (h.net_loss, h.holder_lei), default=None)
    return LadderStep(
        multiplier=m,
        gross_loss=res.totals.gross_loss,
        insurance_recovery=res.totals.insurance_recovery,
        net_loss=res.totals.net_loss,
        breaches=res.totals.breaches,
        worst_holder=worst.holder_lei if worst and worst.net_loss > 0 else None,
    )


def ladder(
    scenario: ScenarioInput, snap: engine.Snapshot, buffers: dict[str, Decimal]
) -> list[LadderStep]:
    return [_step(m, engine.run(scaled(scenario, m), snap, buffers)) for m in LADDER]


def reverse(
    scenario: ScenarioInput, snap: engine.Snapshot, buffers: dict[str, Decimal]
) -> tuple[list[BreakingPoint], Decimal | None]:
    """Smallest severity multiplier that uses up each institution's buffer (interpolated)."""
    curve = [
        (
            m,
            {
                h.holder_lei: h.net_loss
                for h in engine.run(scaled(scenario, m), snap, buffers).holders
            },
        )
        for m in _GRID
    ]
    base = {h.holder_lei: h.net_loss for h in engine.run(scenario, snap, buffers).holders}
    out: list[BreakingPoint] = []
    for lei, buf in sorted(buffers.items()):
        if lei not in base:
            continue
        hit: Decimal | None = None
        prev_m, prev_l = curve[0][0], curve[0][1].get(lei, Decimal(0))
        if prev_l > buf:
            hit = prev_m
        else:
            for m, losses in curve[1:]:
                loss = losses.get(lei, Decimal(0))
                if loss > buf:
                    span = loss - prev_l
                    frac = (buf - prev_l) / span if span > 0 else Decimal(0)
                    hit = prev_m + (m - prev_m) * frac
                    break
                prev_m, prev_l = m, loss
        out.append(
            BreakingPoint(
                holder_lei=lei,
                capital_buffer=buf,
                net_loss=base[lei],
                multiplier=hit.quantize(_TWO, ROUND_HALF_EVEN) if hit is not None else None,
                already_breached=base[lei] > buf,
            )
        )
    out.sort(key=lambda b: (b.multiplier is None, b.multiplier or Decimal(0), b.holder_lei))
    first = next((b.multiplier for b in out if b.multiplier is not None), None)
    return out, first


def cascade(
    scenario: ScenarioInput, snap: engine.Snapshot, buffers: dict[str, Decimal]
) -> list[CascadeRound]:
    """Round 0 is the scenario; each later round adds the institutions that just breached."""
    rounds: list[CascadeRound] = []
    failed: set[str] = set()
    current = scenario
    for n in range(MAX_CASCADE_ROUNDS + 1):
        res = engine.run(current, snap, buffers)
        breached = {h.holder_lei for h in res.holders if h.breach}
        fresh = sorted(breached - failed)
        rounds.append(
            CascadeRound(
                round=n,
                newly_failed=fresh,
                gross_loss=res.totals.gross_loss,
                net_loss=res.totals.net_loss,
                breaches=res.totals.breaches,
            )
        )
        if not fresh or n == MAX_CASCADE_ROUNDS:
            break
        failed |= set(fresh)
        ordered = sorted(failed)
        extra = tuple(  # one shock holds at most MAX_SHOCKS institutions
            DefaultShock(
                kind="default",
                leis=tuple(ordered[i : i + MAX_SHOCKS]),
                loss_given_default_percent=CASCADE_LGD,
                include_group=False,
            )
            for i in range(0, len(ordered), MAX_SHOCKS)
        )
        current = scenario.model_copy(update={"shocks": (*scenario.shocks, *extra)})
    return rounds


def analyse(
    scenario: ScenarioInput, snap: engine.Snapshot, buffers: dict[str, Decimal]
) -> Analysis:
    points, first = reverse(scenario, snap, buffers)
    return Analysis(
        currency=snap.currency,
        ladder=ladder(scenario, snap, buffers),
        reverse=points,
        system_multiplier=first,
        cascade=cascade(scenario, snap, buffers),
    )
