"""Forecast engine: determinism, scenario order, lever effects and the quarter ledger identity."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from reggraph.forecast.engine import (
    PATHS,
    ForecastParams,
    HolderInput,
    quarter_end,
    simulate,
)

HOLDERS = [
    HolderInput("A", 1000.0, 80.0, False, 60.0),
    HolderInput("B", 2000.0, 100.0, False, 0.0),
    HolderInput("C", 500.0, 40.0, True, 30.0),
]


def run(**kw: object) -> dict[str, object]:
    return simulate(HOLDERS, ForecastParams(**kw))  # type: ignore[arg-type]


def test_same_seed_same_answer_and_other_seed_differs() -> None:
    assert run(scenario="adverse") == run(scenario="adverse")
    assert run(scenario="adverse", seed=1) != run(scenario="adverse", seed=2)


def test_scenarios_are_ordered_by_severity() -> None:
    low = {s: run(scenario=s)["summary"]["lowest_p10"] for s in ("baseline", "adverse", "severe")}  # type: ignore[index]
    assert low["baseline"] > low["adverse"] > low["severe"]
    assert PATHS >= 100


def test_levers_help_and_timing_matters() -> None:
    def risk(**kw: object) -> int:
        return run(scenario="severe", **kw)["summary"]["institutions_at_risk"]  # type: ignore[index, no-any-return]

    none = risk()
    assert none >= 1
    assert risk(raise_percent=100, raise_quarter=1) <= none
    assert risk(derisk_percent=50) <= none
    early = run(scenario="severe", raise_percent=60, raise_quarter=1)["summary"]
    late = run(scenario="severe", raise_percent=60, raise_quarter=4)["summary"]
    assert early["institutions_at_risk"] <= late["institutions_at_risk"]  # type: ignore[index]


def test_ledger_adds_up_for_every_quarter() -> None:
    res = run(scenario="adverse", raise_percent=20, raise_quarter=3, derisk_percent=10)
    for row in res["holders"]:  # type: ignore[attr-defined]
        for q in row["ledger"]:
            closing = q["opening"] + q["earnings"] - q["expected_loss"] - q["shock"] + q["raised"]
            assert abs(closing - _next_open(row["ledger"], q)) < 1e-6 or q["q"] == len(
                row["ledger"]
            )
        assert row["bands"][0]["p50"] == row["buffer"]
    assert len(res["series"]) == 13  # type: ignore[arg-type]


def _next_open(ledger: list[dict[str, float]], q: dict[str, float]) -> float:
    nxt = [x for x in ledger if x["q"] == q["q"] + 1]
    return nxt[0]["opening"] if nxt else 0.0


def test_one_holder_alone_matches_the_same_holder_in_the_group() -> None:
    both = run(scenario="adverse")
    solo = simulate([HOLDERS[0]], ForecastParams(scenario="adverse"))
    a = next(r for r in both["holders"] if r["lei"] == "A")  # type: ignore[attr-defined]
    assert a["bands"] == solo["holders"][0]["bands"]  # type: ignore[index]


def test_assumed_buffers_are_counted_and_baseline_has_no_shock() -> None:
    res = run(scenario="baseline")
    assert res["summary"]["assumed_buffers"] == 1  # type: ignore[index]
    assert all(q["shock"] == 0 for r in res["holders"] for q in r["ledger"])  # type: ignore[attr-defined]


def test_recovery_quarter_is_none_zero_or_a_quarter() -> None:
    ok = simulate(
        [HolderInput("Z", 1000.0, 500.0, False, 0.0)], ForecastParams(scenario="baseline")
    )
    assert ok["holders"][0]["recovery_quarter"] == 0  # type: ignore[index]
    hit = simulate(
        [HolderInput("Z", 1000.0, 50.0, False, 400.0)], ForecastParams(scenario="severe")
    )
    assert hit["holders"][0]["recovery_quarter"] is None  # type: ignore[index]
    assert hit["holders"][0]["capital_needed"] > 0  # type: ignore[index]


def test_parameters_are_validated() -> None:
    for bad in (
        {"quarters": 3},
        {"quarters": 21},
        {"raise_percent": 101},
        {"derisk_percent": 51},
        {"seed": -1},
    ):
        with pytest.raises(ValidationError):
            ForecastParams(**bad)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ForecastParams(scenario="apocalypse")  # type: ignore[arg-type]


def test_quarter_labels_roll_over_the_year() -> None:
    assert quarter_end(date(2026, 11, 5), 0) == "2026-12-31"
    assert quarter_end(date(2026, 11, 5), 1) == "2027-03-31"
    assert quarter_end(date(2026, 2, 1), 4) == "2027-03-31"
