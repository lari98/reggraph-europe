"""Advanced stress analysis (0.2.0): severity ladder, reverse stress test, contagion cascade."""

from __future__ import annotations

from decimal import Decimal

from reggraph.stress.analysis import analyse, cascade, ladder, reverse, scaled
from reggraph.stress.models import DefaultShock, FxShock, HaircutShock, ScenarioInput
from tests.conftest import make_lei
from tests.unit.test_stress_engine import LEI_X, LEI_Y, default, row, scenario, snap

D = Decimal
H1, H2 = make_lei("HOLDERONE"), make_lei("HOLDERTWO")


def test_scaling_stays_inside_the_legal_range() -> None:
    sc = scenario(
        default(LEI_X, lgd="60"),
        {"kind": "haircut", "percent": "40", "exposure_type": "loan"},
        {"kind": "fx", "currency": "USD", "change_percent": "-30"},
    )
    big = scaled(sc, D(4))
    d, hc, fx = big.shocks
    assert isinstance(d, DefaultShock) and d.loss_given_default_percent == D("100.00")
    assert isinstance(hc, HaircutShock) and hc.percent == D("100.00")
    assert isinstance(fx, FxShock) and fx.change_percent == D("-100.00")
    small = scaled(sc, D("0.5"))
    assert isinstance(small.shocks[0], DefaultShock)
    assert small.shocks[0].loss_given_default_percent == D("30.00")
    assert isinstance(scaled(sc, D(1)), ScenarioInput) and scaled(sc, D(1)).shocks == sc.shocks


def test_ladder_never_decreases_and_one_x_is_the_scenario() -> None:
    sc = scenario(default(LEI_X, lgd="20"), insurance=False)
    s = snap([row(LEI_X, "100", holder=H1), row(LEI_Y, "50", holder=H1)])
    steps = ladder(sc, s, {H1: D("30")})
    losses = [x.net_loss for x in steps]
    assert losses == sorted(losses)
    one = next(x for x in steps if x.multiplier == 1)
    assert one.net_loss == D("20.00") and one.breaches == 0
    assert steps[-1].breaches == 1 and steps[-1].worst_holder == H1  # 4x: 80 > 30
    assert steps[0].worst_holder == H1


def test_reverse_test_finds_the_multiplier_that_uses_the_buffer_up() -> None:
    sc = scenario(default(LEI_X, lgd="10"), insurance=False)
    s = snap([row(LEI_X, "100", holder=H1), row(LEI_X, "100", holder=H2)])
    points, first = reverse(sc, s, {H1: D("20"), H2: D("40")})
    by = {p.holder_lei: p for p in points}
    # loss is 10 per holder at 1x and grows linearly: 20 -> 2x, 40 -> 4x
    assert abs(by[H1].multiplier - D("2.0")) <= D("0.15") and not by[H1].already_breached  # type: ignore[operator]
    assert abs(by[H2].multiplier - D("4.0")) <= D("0.3")  # type: ignore[operator]
    assert first == by[H1].multiplier and points[0].holder_lei == H1  # weakest first


def test_reverse_test_reports_survivors_and_already_breached() -> None:
    sc = scenario(default(LEI_X, lgd="50"), insurance=False)
    s = snap([row(LEI_X, "100", holder=H1), row(LEI_Y, "100", holder=H2)])
    points, _ = reverse(sc, s, {H1: D("10"), H2: D("10")})
    by = {p.holder_lei: p for p in points}
    assert by[H1].already_breached and by[H1].multiplier is not None
    assert by[H2].multiplier is None and not by[H2].already_breached  # H2 has no loss at all


def test_cascade_spreads_when_a_failed_holder_is_someone_elses_counterparty() -> None:
    # H1 loses on X and breaches; H1 is itself the counterparty of H2, which then breaches too
    sc = scenario(default(LEI_X, lgd="60"), insurance=False)
    s = snap([row(LEI_X, "100", holder=H1), row(H1, "100", holder=H2)])
    rounds = cascade(sc, s, {H1: D("30"), H2: D("30")})
    assert rounds[0].newly_failed == [H1] and rounds[0].breaches == 1
    assert rounds[1].newly_failed == [H2] and rounds[1].breaches == 2
    assert rounds[-1].newly_failed == [] and len(rounds) == 3
    calm = cascade(scenario(default(LEI_X, lgd="1"), insurance=False), s, {H1: D("30")})
    assert len(calm) == 1 and calm[0].newly_failed == []


def test_analyse_bundles_everything_and_is_deterministic() -> None:
    sc = scenario(default(LEI_X, lgd="30"), insurance=False)
    s = snap([row(LEI_X, "100", holder=H1)])
    a, b = analyse(sc, s, {H1: D("50")}), analyse(sc, s, {H1: D("50")})
    assert a == b and a.currency == "EUR" and len(a.ladder) == 6 and a.system_multiplier is not None
