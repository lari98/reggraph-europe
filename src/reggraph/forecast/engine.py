"""Forecast engine: a seeded, explainable simulation of capital buffers over the next quarters.

The model is deliberately small so that every number can be traced to a stated assumption:

    buffer(t) = buffer(t-1) + earnings(t) - expected_loss(t) - stress_shock(t) + capital_raised(t)

* **Earnings** (capital generation) are a share of the exposure each quarter.
* **Expected loss** is the exposure times a loss rate; it is multiplied by a random factor that has
  a shared (systemic) and an own (idiosyncratic) part, so institutions move together but not
  identically.
* **Stress shock** is the net loss of a stored stress run, scaled by the scenario severity, and it
  lands in one quarter; afterwards the loss rate stays elevated and fades out.
* **Levers** (what management can do): a capital raise, and de-risking (shrinking the exposure).

The simulation uses a fixed seed and common random numbers, so changing a lever changes the
result only through the lever - the comparison of recovery options is fair and repeatable.
This is a planning aid built on stated assumptions, not a prediction.
"""

from __future__ import annotations

import math
import random
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

PATHS: Final = 200  # simulated futures per run
SHOCK_QUARTER: Final = 2
SHOCK_VOL: Final = 0.25
SYSTEMIC: Final = 0.6  # share of the loss-rate randomness that all institutions share
DEFAULT_BUFFER_RATIO: Final = 0.08  # used when a run had no buffer for an institution
DERISK_QUARTERS: Final = 4
RISK_LINE: Final = 0.10  # an institution is "at risk" if it breaches in 10% of futures or more

Scenario = Literal["baseline", "adverse", "severe"]


class _M(BaseModel):
    model_config = ConfigDict(frozen=True)


class Assumptions(_M):
    """The stated assumptions behind one scenario (all per year unless noted)."""

    exposure_growth: float
    loss_rate: float
    earnings: float
    shock_severity: float  # share of the stress run's net loss that lands in quarter 2
    loss_multiplier: float  # loss rate in the shock quarter, as a multiple of normal
    fade: float  # how much of the elevated loss rate remains after one more quarter
    volatility: float


PRESETS: Final[dict[str, Assumptions]] = {
    "baseline": Assumptions(
        exposure_growth=0.03,
        loss_rate=0.0035,
        earnings=0.009,
        shock_severity=0.0,
        loss_multiplier=1.0,
        fade=0.5,
        volatility=0.35,
    ),
    "adverse": Assumptions(
        exposure_growth=0.0,
        loss_rate=0.005,
        earnings=0.0085,
        shock_severity=0.5,
        loss_multiplier=1.5,
        fade=0.6,
        volatility=0.5,
    ),
    "severe": Assumptions(
        exposure_growth=-0.03,
        loss_rate=0.006,
        earnings=0.007,
        shock_severity=1.0,
        loss_multiplier=2.5,
        fade=0.7,
        volatility=0.7,
    ),
}


class ForecastParams(_M):
    scenario: Scenario = "adverse"
    quarters: int = Field(default=12, ge=4, le=20)
    seed: int = Field(default=2026, ge=0, le=2_147_483_647)
    raise_percent: float = Field(default=0, ge=0, le=100)  # of each opening buffer
    raise_quarter: int = Field(default=1, ge=1, le=20)
    derisk_percent: float = Field(default=0, ge=0, le=50)  # of each exposure, over 4 quarters


@dataclass(frozen=True)
class HolderInput:
    lei: str
    exposure: float
    buffer: float
    buffer_assumed: bool
    stress_net_loss: float


@dataclass
class _Acc:
    """Per-quarter sums over all futures for one institution (means are sums / PATHS)."""

    opening: list[float] = field(default_factory=list)
    earnings: list[float] = field(default_factory=list)
    loss: list[float] = field(default_factory=list)
    shock: list[float] = field(default_factory=list)
    raised: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)


def quarter_end(start: date, q: int) -> str:
    """Last day of the q-th quarter after ``start`` (quarter 0 is the starting quarter)."""
    month_index = start.month - 1 + 3 * q
    year, month = start.year + month_index // 12, month_index % 12 + 1
    month = (month - 1) // 3 * 3 + 3
    return date(year, month, monthrange(year, month)[1]).isoformat()


def _pct(sorted_values: list[float], p: float) -> float:
    pos = p * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _systemic(seed: int, quarters: int) -> list[list[float]]:
    rng = random.Random(f"{seed}:systemic")  # noqa: S311  # nosec B311 - simulation, not security
    return [[rng.gauss(0, 1) for _ in range(quarters + 1)] for _ in range(PATHS)]


def simulate(
    holders: list[HolderInput],
    params: ForecastParams,
    raise_amounts: dict[str, float] | None = None,
) -> dict[str, object]:
    """Run all futures and summarise. ``raise_amounts`` overrides the uniform raise per holder."""
    a = PRESETS[params.scenario]
    n = params.quarters
    sysd = _systemic(params.seed, n)
    qg, qe = a.exposure_growth / 4, a.earnings / 4
    agg_paths = [[0.0] * (n + 1) for _ in range(PATHS)]
    breached_by = [[0] * (n + 1) for _ in range(PATHS)]  # holders already breached, per path
    rows: list[dict[str, object]] = []
    agg_acc = _Acc(*([0.0] * n for _ in range(6)))
    for h in holders:
        rng = random.Random(f"{params.seed}:{h.lei}")  # noqa: S311  # nosec B311
        acc = _Acc(*([0.0] * n for _ in range(6)))
        raise_total = (raise_amounts or {}).get(h.lei, h.buffer * params.raise_percent / 100)
        grid = [[0.0] * (n + 1) for _ in range(PATHS)]
        first_breach = 0
        for p in range(PATHS):
            buf, exp, hit = h.buffer, h.exposure, False
            grid[p][0] = buf
            for q in range(1, n + 1):
                prev_exp = exp
                if q <= DERISK_QUARTERS:
                    exp *= 1 - params.derisk_percent / 100 / DERISK_QUARTERS
                exp *= 1 + qg
                rate = a.loss_rate
                if q >= SHOCK_QUARTER:
                    rate *= 1 + (a.loss_multiplier - 1) * a.fade ** (q - SHOCK_QUARTER)
                z = math.exp(
                    a.volatility
                    * (math.sqrt(SYSTEMIC) * sysd[p][q] + math.sqrt(1 - SYSTEMIC) * rng.gauss(0, 1))
                    - a.volatility**2 / 2
                )
                loss = prev_exp * rate / 4 * z
                shock = 0.0
                if q == SHOCK_QUARTER and a.shock_severity > 0:
                    zs = math.exp(SHOCK_VOL * rng.gauss(0, 1) - SHOCK_VOL**2 / 2)
                    shock = a.shock_severity * h.stress_net_loss * zs * (prev_exp / h.exposure)
                earn = prev_exp * qe
                add = raise_total if q == params.raise_quarter else 0.0
                opening = buf
                buf = opening + earn - loss - shock + add
                grid[p][q] = buf
                if buf < 0 and not hit:
                    hit = True
                    first_breach += 1
                if hit:
                    breached_by[p][q] += 1
                for arr, val in (
                    (acc.opening, opening),
                    (acc.earnings, earn),
                    (acc.loss, loss),
                    (acc.shock, shock),
                    (acc.raised, add),
                    (acc.exposure, exp),
                ):
                    arr[q - 1] += val
            for q in range(n + 1):
                agg_paths[p][q] += grid[p][q]
        for src, dst in zip(
            (acc.opening, acc.earnings, acc.loss, acc.shock, acc.raised, acc.exposure),
            (
                agg_acc.opening,
                agg_acc.earnings,
                agg_acc.loss,
                agg_acc.shock,
                agg_acc.raised,
                agg_acc.exposure,
            ),
            strict=True,
        ):
            for i in range(n):
                dst[i] += src[i]
        bands = []
        for q in range(n + 1):
            col = sorted(grid[p][q] for p in range(PATHS))
            bands.append((_pct(col, 0.1), _pct(col, 0.5), _pct(col, 0.9)))
        rows.append(_holder_row(h, params, acc, bands, first_breach))
    series = _series(params, holders, agg_acc, agg_paths, breached_by)
    return {"series": series, "holders": rows, "summary": _summary(holders, series, rows)}


def _holder_row(
    h: HolderInput,
    params: ForecastParams,
    acc: _Acc,
    bands: list[tuple[float, float, float]],
    first_breach: int,
) -> dict[str, object]:
    n = params.quarters
    p50 = [b[1] for b in bands]
    low_q = min(range(n + 1), key=lambda q: p50[q])
    recovery: int | None = 0
    if p50[low_q] < h.buffer:
        recovery = next((q for q in range(low_q, n + 1) if p50[q] >= h.buffer), None)
    need = max(0.0, -min(b[0] for b in bands))
    ledger = [
        {
            "q": q + 1,
            "exposure": acc.exposure[q] / PATHS,
            "opening": acc.opening[q] / PATHS,
            "earnings": acc.earnings[q] / PATHS,
            "expected_loss": acc.loss[q] / PATHS,
            "shock": acc.shock[q] / PATHS,
            "raised": acc.raised[q] / PATHS,
            "p10": bands[q + 1][0],
            "p50": bands[q + 1][1],
            "p90": bands[q + 1][2],
        }
        for q in range(n)
    ]
    return {
        "lei": h.lei,
        "exposure": h.exposure,
        "buffer": h.buffer,
        "buffer_assumed": h.buffer_assumed,
        "stress_net_loss": h.stress_net_loss,
        "breach_probability": first_breach / PATHS,
        "lowest_p50": p50[low_q],
        "lowest_quarter": low_q,
        "recovery_quarter": recovery,
        "capital_needed": need,
        "bands": [{"q": q, "p10": b[0], "p50": b[1], "p90": b[2]} for q, b in enumerate(bands)],
        "ledger": ledger,
    }


def _series(
    params: ForecastParams,
    holders: list[HolderInput],
    acc: _Acc,
    agg_paths: list[list[float]],
    breached_by: list[list[int]],
) -> list[dict[str, object]]:
    n = params.quarters
    out: list[dict[str, object]] = []
    total = sum(h.buffer for h in holders)
    for q in range(n + 1):
        col = sorted(agg_paths[p][q] for p in range(PATHS))
        entry: dict[str, object] = {
            "q": q,
            "p10": _pct(col, 0.1),
            "p50": _pct(col, 0.5),
            "p90": _pct(col, 0.9),
            "at_risk_expected": sum(breached_by[p][q] for p in range(PATHS)) / PATHS,
            "any_breach_probability": sum(1 for p in range(PATHS) if breached_by[p][q]) / PATHS,
        }
        if q == 0:
            entry.update(p10=total, p50=total, p90=total, at_risk_expected=0.0)
        else:
            i = q - 1
            entry.update(
                exposure=acc.exposure[i] / PATHS,
                opening=acc.opening[i] / PATHS,
                earnings=acc.earnings[i] / PATHS,
                expected_loss=acc.loss[i] / PATHS,
                shock=acc.shock[i] / PATHS,
                raised=acc.raised[i] / PATHS,
            )
        out.append(entry)
    return out


def _summary(
    holders: list[HolderInput], series: list[dict[str, object]], rows: list[dict[str, object]]
) -> dict[str, object]:
    def num(entry: dict[str, object], key: str) -> float:
        value = entry[key]
        if not isinstance(value, float | int):
            raise TypeError(key)
        return float(value)

    start = num(series[0], "p50")
    low = min(series, key=lambda e: num(e, "p10"))
    p50_low = min(series, key=lambda e: num(e, "p50"))
    recovery: int | None = 0
    if num(p50_low, "p50") < start:
        recovery = next(
            (int(num(e, "q")) for e in series[int(num(p50_low, "q")) :] if num(e, "p50") >= start),
            None,
        )
    at_risk = sum(1 for r in rows if num(r, "breach_probability") >= RISK_LINE)
    return {
        "buffer_now": start,
        "lowest_p10": num(low, "p10"),
        "lowest_p10_quarter": int(num(low, "q")),
        "lowest_p50": num(p50_low, "p50"),
        "lowest_p50_quarter": int(num(p50_low, "q")),
        "recovery_quarter": recovery,
        "institutions": len(holders),
        "institutions_at_risk": at_risk,
        "capital_needed": sum(num(r, "capital_needed") for r in rows),
        "assumed_buffers": sum(1 for h in holders if h.buffer_assumed),
        "end_p50": num(series[-1], "p50"),
    }
