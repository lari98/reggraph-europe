"""Forecast service: reads a stored stress run, simulates the future, compares recovery options.

Read-only: nothing is stored, so the same inputs always give the same answer (fixed seed).
Needs ``stress:read``; the stress run supplies exposures, buffers and the shock size.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any, Final

from reggraph.core.errors import ValidationFailure
from reggraph.forecast.engine import (
    DEFAULT_BUFFER_RATIO,
    PRESETS,
    ForecastParams,
    HolderInput,
    quarter_end,
    simulate,
)
from reggraph.registry.lei import validate_lei
from reggraph.security.rbac import Permission, Principal
from reggraph.stress.store import StressStore

MAX_HOLDERS: Final = 200
RAISE_MARGIN: Final = 1.1  # a targeted raise covers the shortfall plus 10%
DERISK_STEP: Final = 15.0


def _num(value: object) -> float:
    return float(Decimal(str(value)))


class ForecastLab:
    def __init__(self, store: StressStore) -> None:
        self._store = store

    # ---------------------------------------------------------------- inputs
    def _inputs(
        self, who: Principal, run_id: int, holder: str | None
    ) -> tuple[list[HolderInput], date, str]:
        who.require(Permission.STRESS_READ)
        stored = self._store.get_run(who, run_id)
        try:
            result = json.loads(stored.result_json)
            rows = result["holders"]
            currency = str(result["currency"])
            start = date.fromisoformat(stored.record.as_of)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValidationFailure("stored run cannot be forecast") from exc
        if len(rows) > MAX_HOLDERS:
            raise ValidationFailure("too many institutions to forecast")
        wanted = validate_lei(holder) if holder else None
        out: list[HolderInput] = []
        for r in rows:
            if wanted and r["holder_lei"] != wanted:
                continue
            exposure = _num(r["exposure"])
            if exposure <= 0:
                continue
            raw = r.get("capital_buffer")
            assumed = raw is None
            buffer = exposure * DEFAULT_BUFFER_RATIO if assumed else _num(raw)
            out.append(HolderInput(r["holder_lei"], exposure, buffer, assumed, _num(r["net_loss"])))
        if wanted and not out:
            raise ValidationFailure("that institution is not part of this run")
        return out, start, currency

    # ---------------------------------------------------------------- forecast
    def forecast(
        self, who: Principal, run_id: int, params: ForecastParams, holder: str | None = None
    ) -> dict[str, Any]:
        holders, start, currency = self._inputs(who, run_id, holder)
        res = simulate(holders, params)
        return self._wrap(run_id, params, start, currency, res)

    @staticmethod
    def _wrap(
        run_id: int, params: ForecastParams, start: date, currency: str, res: dict[str, object]
    ) -> dict[str, Any]:
        a = PRESETS[params.scenario]
        series: list[dict[str, Any]] = res["series"]  # type: ignore[assignment]
        for entry in series:
            entry["date"] = quarter_end(start, int(entry["q"])) if entry["q"] else start.isoformat()
        return {
            "run_id": run_id,
            "currency": currency,
            "as_of": start.isoformat(),
            "scenario": params.scenario,
            "params": params.model_dump(),
            "assumptions": a.model_dump(),
            "dates": [e["date"] for e in series],
            **res,
        }

    # ---------------------------------------------------------------- recovery options
    def plan(self, who: Principal, run_id: int, params: ForecastParams) -> dict[str, Any]:
        """Compare what management can do, all on the same simulated futures."""
        holders, start, currency = self._inputs(who, run_id, None)
        base = params.model_copy(update={"raise_percent": 0, "derisk_percent": 0})
        none = simulate(holders, base)
        need = {str(r["lei"]): float(r["capital_needed"]) for r in none["holders"]}  # type: ignore[attr-defined]
        targeted = {k: v * RAISE_MARGIN for k, v in need.items()}
        half = {k: v * 0.5 for k, v in targeted.items()}
        options = [
            ("none", "No action", none, 0.0),
            (
                "raise_early",
                "Raise capital now (quarter 1)",
                simulate(holders, base.model_copy(update={"raise_quarter": 1}), targeted),
                sum(targeted.values()),
            ),
            (
                "raise_late",
                "Raise capital after the shock (quarter 3)",
                simulate(holders, base.model_copy(update={"raise_quarter": 3}), targeted),
                sum(targeted.values()),
            ),
            (
                "derisk",
                f"Reduce exposure by {DERISK_STEP:.0f}%",
                simulate(holders, base.model_copy(update={"derisk_percent": DERISK_STEP})),
                0.0,
            ),
            (
                "combined",
                f"Reduce exposure by {DERISK_STEP:.0f}% and raise half the gap now",
                simulate(
                    holders,
                    base.model_copy(update={"derisk_percent": DERISK_STEP, "raise_quarter": 1}),
                    half,
                ),
                sum(half.values()),
            ),
        ]
        rows = []
        for key, title, res, raised in options:
            s: dict[str, Any] = res["summary"]  # type: ignore[assignment]
            rows.append(
                {
                    "key": key,
                    "title": title,
                    "capital_raised": raised,
                    "lowest_p10": s["lowest_p10"],
                    "lowest_p10_quarter": s["lowest_p10_quarter"],
                    "institutions_at_risk": s["institutions_at_risk"],
                    "recovery_quarter": s["recovery_quarter"],
                    "end_p50": s["end_p50"],
                }
            )
        return {
            "run_id": run_id,
            "currency": currency,
            "as_of": start.isoformat(),
            "scenario": params.scenario,
            "quarters": params.quarters,
            "needs": need,
            "options": rows,
        }
