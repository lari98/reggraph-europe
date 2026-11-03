"""Pure exposure maths: edges, concentration, neighbourhood (no database, no HTTP)."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from reggraph.exposure.analytics import (
    Edge,
    Flow,
    aggregate_edges,
    concentration,
    neighbourhood,
)

D = Decimal


def _f(h: str, c: str, amount: str) -> Flow:
    return Flow(h, c, D(amount))


def test_edges_sum_and_order() -> None:
    edges = aggregate_edges(
        [_f("A", "B", "10"), _f("A", "B", "5"), _f("A", "C", "40"), _f("B", "A", "15")]
    )
    assert [(e.holder, e.counterparty, e.total, e.count) for e in edges] == [
        ("A", "C", D(40), 1),
        ("A", "B", D(15), 2),  # tie with B>A broken by holder LEI
        ("B", "A", D(15), 1),
    ]


def test_concentration_values() -> None:
    flows = [_f("H", "X", "50"), _f("H", "Y", "30"), _f("H", "Z", "20")]
    c = concentration(flows, lambda f: f.counterparty)
    assert c.total == D(100)
    assert [s.key for s in c.shares] == ["X", "Y", "Z"]
    assert c.top1 == D("0.5000") and c.top3 == D("1.0000")
    assert c.hhi == D("3800.00")  # 0.25 + 0.09 + 0.04 = 0.38


def test_concentration_by_group_merges_members() -> None:
    flows = [_f("H", "S1", "30"), _f("H", "S2", "30"), _f("H", "Z", "40")]
    group = {"S1": "P", "S2": "P", "Z": "Z"}
    c = concentration(flows, lambda f: group[f.counterparty])
    assert [(s.key, s.total, s.count) for s in c.shares] == [("P", D(60), 2), ("Z", D(40), 1)]


def test_concentration_empty_and_single() -> None:
    assert concentration([], lambda f: f.counterparty).total == 0
    one = concentration([_f("H", "X", "7")], lambda f: f.counterparty)
    assert one.hhi == D("10000.00") and one.top1 == D("1.0000")


def test_zero_amounts_do_not_divide_by_zero() -> None:
    c = concentration([_f("H", "X", "0")], lambda f: f.counterparty)
    assert c.total == 0 and c.shares == ()


@given(
    st.lists(
        st.tuples(st.sampled_from("ABCDE"), st.integers(min_value=1, max_value=10**9)),
        min_size=1,
        max_size=40,
    )
)
def test_concentration_properties(rows: list[tuple[str, int]]) -> None:
    c = concentration([Flow("H", k, D(v)) for k, v in rows], lambda f: f.counterparty)
    assert D(0) < c.hhi <= D(10000)
    assert abs(sum(s.share for s in c.shares) - 1) < D("0.001")
    assert c.top1 <= c.top3 <= D(1)
    assert c.total == sum(D(v) for _, v in rows)


def _edges() -> list[Edge]:
    return [
        Edge("A", "B", D(1), 1),
        Edge("B", "C", D(1), 1),
        Edge("C", "D", D(1), 1),
        Edge("X", "Y", D(1), 1),
    ]


def test_neighbourhood_depth_and_direction() -> None:
    hood = neighbourhood(_edges(), "B", 1)
    assert hood.depth_of == {"B": 0, "A": 1, "C": 1}  # both directions
    assert {(e.holder, e.counterparty) for e in hood.edges} == {("A", "B"), ("B", "C")}
    assert neighbourhood(_edges(), "B", 2).depth_of["D"] == 2
    assert "X" not in neighbourhood(_edges(), "B", 4).depth_of


def test_neighbourhood_bounds_and_determinism() -> None:
    assert neighbourhood(_edges(), "B", 0).depth_of == {"B": 0}
    assert neighbourhood(_edges(), "B", 99).depth_of == neighbourhood(_edges(), "B", 4).depth_of
    small = neighbourhood(_edges(), "B", 3, max_nodes=2)
    assert small.truncated and len(small.depth_of) == 2
    assert neighbourhood(_edges(), "B", 3) == neighbourhood(list(reversed(_edges())), "B", 3)


def test_unknown_root_is_just_itself() -> None:
    hood = neighbourhood(_edges(), "NOPE", 3)
    assert hood.depth_of == {"NOPE": 0} and hood.edges == ()
