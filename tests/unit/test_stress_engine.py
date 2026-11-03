"""The stress engine, scenario validation and the result signer (no database, no HTTP)."""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from reggraph.security.signing import ResultSigner
from reggraph.stress import ScenarioInput
from reggraph.stress.engine import SnapCover, SnapRow, Snapshot, run
from tests.conftest import make_lei

D = Decimal
H = "H"
INS = make_lei("INSX")


def row(
    cpty: str, amount: str, *, ccy: str = "EUR", typ: str = "loan", jur: str = "DE", holder: str = H
) -> SnapRow:
    return SnapRow(
        holder_lei=holder,
        counterparty_lei=cpty,
        counterparty_jurisdiction=jur,
        exposure_type=typ,
        currency=ccy,
        amount=D(amount),
    )


def snap(
    rows: list[SnapRow],
    covers: list[SnapCover] | None = None,
    members: dict[str, list[str]] | None = None,
) -> Snapshot:
    return Snapshot(
        as_of="2026-03-02",
        currency="EUR",
        max_batch=1,
        rows=rows,
        covers=covers or [],
        group_members=members or {},
        lineage={},
    )


def scenario(*shocks: dict[str, Any], insurance: bool = True) -> ScenarioInput:
    return ScenarioInput.model_validate(
        {"scenario_key": "S1", "title": "t", "shocks": list(shocks), "use_insurance": insurance}
    )


LEI_X, LEI_Y = "5493001KJTIIGC8Y1R12", "529900T8BM49AURSDO55"


def default(*leis: str, lgd: str = "40", group: bool = True) -> dict[str, Any]:
    return {
        "kind": "default",
        "leis": list(leis),
        "loss_given_default_percent": lgd,
        "include_group": group,
    }


def test_default_shock_loses_lgd_share() -> None:
    res = run(scenario(default(LEI_X)), snap([row(LEI_X, "100"), row(LEI_Y, "100")]))
    assert res.totals.gross_loss == D("40.00") and res.holders[0].net_loss == D("40.00")
    assert res.holders[0].net_loss_ratio == D("0.2000")
    assert [(c.counterparty_lei, c.gross_loss) for c in res.top_contributors] == [
        (LEI_X, D("40.00"))
    ]


def test_shocks_combine_multiplicatively_and_order_is_irrelevant() -> None:
    shocks = [
        default(LEI_X, lgd="50"),
        {"kind": "haircut", "percent": "50", "exposure_type": "loan"},
    ]
    a = run(scenario(*shocks), snap([row(LEI_X, "100")]))
    b = run(scenario(*reversed(shocks)), snap([row(LEI_X, "100")]))
    assert a.totals.gross_loss == D("75.00")  # survives 0.5 x 0.5
    assert a == b


def test_fx_shock_only_hurts_on_a_fall() -> None:
    usd = [row(LEI_X, "100", ccy="USD"), row(LEI_Y, "100")]
    fall = run(scenario({"kind": "fx", "currency": "usd", "change_percent": "-20"}), snap(usd))
    rise = run(scenario({"kind": "fx", "currency": "USD", "change_percent": "20"}), snap(usd))
    assert fall.totals.gross_loss == D("20.00") and rise.totals.gross_loss == D(0)


def test_haircut_filters_are_anded() -> None:
    rows = [
        row(LEI_X, "100", typ="bond", jur="CH"),
        row(LEI_Y, "100", typ="loan", jur="CH"),
        row("Z", "100", typ="bond", jur="DE"),
    ]
    both = run(
        scenario(
            {
                "kind": "haircut",
                "percent": "10",
                "exposure_type": "bond",
                "counterparty_jurisdiction": "ch",
            }
        ),
        snap(rows),
    )
    assert both.totals.gross_loss == D("10.00")
    only_ch = run(
        scenario({"kind": "haircut", "percent": "10", "counterparty_jurisdiction": "CH"}),
        snap(rows),
    )
    assert only_ch.totals.gross_loss == D("20.00")


def test_group_members_default_together_unless_switched_off() -> None:
    rows = [row(LEI_X, "100"), row("SUB", "100")]
    members = {LEI_X: ["SUB"]}
    assert run(
        scenario(default(LEI_X, lgd="100")), snap(rows, members=members)
    ).totals.gross_loss == D("200.00")
    assert run(
        scenario(default(LEI_X, lgd="100", group=False)), snap(rows, members=members)
    ).totals.gross_loss == D("100.00")


def test_insurance_recovery_and_insurer_failure() -> None:
    rows = [row(LEI_X, "100")]
    cover = [
        SnapCover(contract_ref="P", version_no=1, insurer_lei=INS, insured_lei=H, amount=D("30"))
    ]
    res = run(scenario(default(LEI_X)), snap(rows, cover))
    h = res.holders[0]
    assert (h.gross_loss, h.insurance_recovery, h.net_loss) == (D("40.00"), D("30.00"), D("10.00"))
    failed = run(
        scenario(default(LEI_X), {"kind": "insurer_failure", "leis": [LEI_Y]}),
        snap(rows, [cover[0].model_copy(update={"insurer_lei": LEI_Y})]),
    )
    assert failed.holders[0].insurance_recovery == D(0)
    half = run(scenario(default(LEI_X), default(INS, lgd="50")), snap(rows, cover))
    assert half.holders[0].insurance_recovery == D("15.00")  # the insurer itself lost half
    off = run(scenario(default(LEI_X), insurance=False), snap(rows, cover))
    assert off.holders[0].insurance_recovery == D(0)


def test_recovery_never_exceeds_loss() -> None:
    cover = [
        SnapCover(contract_ref="P", version_no=1, insurer_lei=INS, insured_lei=H, amount=D("1000"))
    ]
    h = run(scenario(default(LEI_X)), snap([row(LEI_X, "100")], cover)).holders[0]
    assert h.insurance_recovery == h.gross_loss and h.net_loss == D(0)


def test_capital_buffer_breach() -> None:
    res = run(scenario(default(LEI_X)), snap([row(LEI_X, "100")]), {H: D("39.99")})
    assert res.holders[0].breach and res.totals.breaches == 1
    assert (
        not run(scenario(default(LEI_X)), snap([row(LEI_X, "100")]), {H: D("40.00")})
        .holders[0]
        .breach
    )


def test_rounding_to_minor_units() -> None:
    res = run(
        scenario({"kind": "haircut", "percent": "33.33", "exposure_type": "loan"}),
        snap([row(LEI_X, "0.05")]),
    )
    assert res.totals.gross_loss == D("0.02")  # 0.016665 -> 0.02


@given(st.integers(min_value=0, max_value=10_000))
def test_row_order_never_changes_the_result(seed: int) -> None:
    rows = [
        row(c, str(10 * (i + 1)), typ=t, jur=j, holder=h)
        for i, (c, t, j, h) in enumerate(
            [
                (LEI_X, "loan", "DE", "H1"),
                (LEI_Y, "bond", "CH", "H1"),
                ("Z", "loan", "CH", "H2"),
                (LEI_X, "bond", "DE", "H2"),
            ]
        )
    ]
    shuffled = rows[:]
    random.Random(seed).shuffle(shuffled)  # noqa: S311 - test data order, not security
    sc = scenario(
        default(LEI_X), {"kind": "haircut", "percent": "10", "counterparty_jurisdiction": "CH"}
    )
    assert run(sc, snap(rows)) == run(sc, snap(shuffled))


@pytest.mark.parametrize(
    "shock",
    [
        {"kind": "fx", "currency": "USD", "change_percent": "1e2"},
        {"kind": "fx", "currency": "USD", "change_percent": 5.5},
        {"kind": "fx", "currency": "USD", "change_percent": "-100.01"},
        {"kind": "fx", "currency": "XXX", "change_percent": "5"},
        {"kind": "default", "leis": [LEI_X], "loss_given_default_percent": "0"},
        {"kind": "default", "leis": [LEI_X], "loss_given_default_percent": "100.01"},
        {"kind": "default", "leis": [LEI_X], "loss_given_default_percent": "NaN"},
        {"kind": "default", "leis": [], "loss_given_default_percent": "10"},
        {"kind": "default", "leis": ["5493001KJTIIGC8Y1R13"], "loss_given_default_percent": "10"},
        {"kind": "haircut", "percent": "10"},
        {"kind": "haircut", "percent": "-5", "exposure_type": "loan"},
        {"kind": "insurer_failure", "leis": []},
        {"kind": "meteor", "percent": "10"},
        {"kind": "default", "leis": [LEI_X], "loss_given_default_percent": "10", "surprise": 1},
    ],
)
def test_bad_shocks_are_rejected(shock: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        scenario(shock)


def test_scenario_limits_and_hash() -> None:
    with pytest.raises(ValidationError):
        scenario()
    with pytest.raises(ValidationError):
        scenario(*[default(LEI_X)] * 51)
    a, b = scenario(default(LEI_X)), scenario(default(LEI_X))
    assert a.content_hash() == b.content_hash() != scenario(default(LEI_X, lgd="41")).content_hash()


def test_signer_roundtrip_rotation_and_forgery() -> None:
    old, new = b"o" * 32, b"n" * 32
    s_old = ResultSigner({"k1": old}, "k1")
    sig = s_old.sign("msg")
    assert sig.startswith("k1.") and s_old.verify("msg", sig)
    rotated = ResultSigner({"k1": old, "k2": new}, "k2")
    assert rotated.verify("msg", sig) and rotated.sign("msg").startswith("k2.")
    assert not rotated.verify("other", sig)
    assert not ResultSigner({"k2": new}, "k2").verify("msg", sig)  # old key removed = revoked
    for bad in (
        "",
        "k1",
        "k1.",
        "k1.zz",
        "nokey.abc",
        sig[:-1] + ("0" if sig[-1] != "0" else "1"),
        "." + sig,
    ):
        assert not s_old.verify("msg", bad)
    with pytest.raises(ValueError):
        ResultSigner({"k1": old}, "missing")
