"""Pure graph and concentration maths over already-converted exposure flows (no I/O).

All money here is a ``Decimal`` in one common currency. Shares are fractions rounded half-even
to 4 places; the Herfindahl-Hirschman index (HHI) is on the usual 0..10 000 scale.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

_SHARE_Q: Final = Decimal("0.0001")
_HHI_Q: Final = Decimal("0.01")
_ZERO: Final = Decimal(0)
MAX_DEPTH: Final = 4
MAX_NODES: Final = 500


@dataclass(frozen=True)
class Flow:
    holder: str
    counterparty: str
    amount: Decimal  # in the common (target) currency


@dataclass(frozen=True)
class Edge:
    holder: str
    counterparty: str
    total: Decimal
    count: int


@dataclass(frozen=True)
class Share:
    key: str
    total: Decimal
    share: Decimal
    count: int


@dataclass(frozen=True)
class Concentration:
    total: Decimal
    shares: tuple[Share, ...]  # largest first
    hhi: Decimal
    top1: Decimal
    top3: Decimal


@dataclass(frozen=True)
class Neighbourhood:
    root: str
    depth_of: dict[str, int]  # node -> shortest distance from the root (undirected)
    edges: tuple[Edge, ...]
    truncated: bool


def aggregate_edges(flows: list[Flow]) -> list[Edge]:
    """Sum flows per (holder, counterparty); biggest first, ties broken by LEI for determinism."""
    sums: dict[tuple[str, str], tuple[Decimal, int]] = {}
    for f in flows:
        total, count = sums.get((f.holder, f.counterparty), (_ZERO, 0))
        sums[(f.holder, f.counterparty)] = (total + f.amount, count + 1)
    edges = [Edge(h, c, t, n) for (h, c), (t, n) in sums.items()]
    edges.sort(key=lambda e: (-e.total, e.holder, e.counterparty))
    return edges


def concentration(flows: list[Flow], key_of: Callable[[Flow], str]) -> Concentration:
    """How concentrated the exposure is across ``key_of(flow)`` (counterparty or its group)."""
    totals: dict[str, tuple[Decimal, int]] = {}
    for f in flows:
        total, count = totals.get(key_of(f), (_ZERO, 0))
        totals[key_of(f)] = (total + f.amount, count + 1)
    grand = sum((t for t, _ in totals.values()), _ZERO)
    if grand == 0:
        return Concentration(_ZERO, (), _ZERO, _ZERO, _ZERO)
    shares = [
        Share(k, t, (t / grand).quantize(_SHARE_Q, ROUND_HALF_EVEN), n)
        for k, (t, n) in totals.items()
    ]
    shares.sort(key=lambda s: (-s.total, s.key))
    hhi = sum(((t / grand) ** 2 for t, _ in totals.values()), _ZERO) * 10_000
    top3 = sum((s.total for s in shares[:3]), _ZERO) / grand
    return Concentration(
        total=grand,
        shares=tuple(shares),
        hhi=hhi.quantize(_HHI_Q, ROUND_HALF_EVEN),
        top1=(shares[0].total / grand).quantize(_SHARE_Q, ROUND_HALF_EVEN),
        top3=top3.quantize(_SHARE_Q, ROUND_HALF_EVEN),
    )


def neighbourhood(
    edges: list[Edge], root: str, depth: int, max_nodes: int = MAX_NODES
) -> Neighbourhood:
    """Breadth-first walk over exposure edges in either direction, bounded in depth and size."""
    depth = max(0, min(depth, MAX_DEPTH))
    adjacent: dict[str, set[str]] = {}
    for e in edges:
        adjacent.setdefault(e.holder, set()).add(e.counterparty)
        adjacent.setdefault(e.counterparty, set()).add(e.holder)
    seen: dict[str, int] = {root: 0}
    queue: deque[str] = deque([root])
    truncated = False
    while queue:
        node = queue.popleft()
        if seen[node] >= depth:
            continue
        for nxt in sorted(adjacent.get(node, ())):  # sorted: same input, same result
            if nxt in seen:
                continue
            if len(seen) >= max_nodes:
                truncated = True
                continue
            seen[nxt] = seen[node] + 1
            queue.append(nxt)
    inside = tuple(
        sorted(
            (e for e in edges if e.holder in seen and e.counterparty in seen),
            key=lambda e: (-e.total, e.holder, e.counterparty),
        )
    )
    return Neighbourhood(root, seen, inside, truncated)
