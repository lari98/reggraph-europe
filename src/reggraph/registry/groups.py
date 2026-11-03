"""Group-structure logic on plain data (no database): cycles, percentages, control, parents.

Simplification, stated openly: *control* here means "holds more than 50 % directly, or is a
branch". Real prudential or accounting consolidation (for example CRR Art. 18 or IFRS 10) can
include other tests such as dominant influence; treat the result as an indicative perimeter,
to be confirmed by the control owner.

====  ====================================================================================
G1    A link period is inclusive on both ends; ``None`` for the end means open-ended.
G2    Ownership must stay acyclic among links whose periods overlap the new one.
G3    At no single day may the percentages held in one child add up to more than 100.
G4    Traversals are depth- and size-capped so a hostile or broken graph cannot exhaust memory.
====  ====================================================================================
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from reggraph.core.errors import ValidationFailure

MAX_DEPTH: Final = 25
MAX_MEMBERS: Final = 5000
CONTROL_THRESHOLD: Final = Decimal(50)
_HUNDRED: Final = Decimal(100)
_FOUR_DP: Final = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class Link:
    link_id: int
    parent: str
    child: str
    percent: Decimal
    kind: str
    effective_from: date
    effective_until: date | None

    def active_on(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_until is None or day <= self.effective_until
        )

    def overlaps(self, start: date, until: date | None) -> bool:
        return self.effective_from <= (until or date.max) and start <= (
            self.effective_until or date.max
        )

    @property
    def controlling(self) -> bool:
        return self.kind == "branch" or self.percent > CONTROL_THRESHOLD


@dataclass(frozen=True, slots=True)
class Member:
    lei: str
    depth: int
    via: str  # the direct parent inside the group
    direct_percent: Decimal
    effective_percent: Decimal  # product of percentages along the controlling chain


@dataclass(frozen=True, slots=True)
class Participation:
    holder: str  # a group member
    held: str
    percent: Decimal


@dataclass(frozen=True, slots=True)
class GroupView:
    root: str
    members: tuple[Member, ...]
    participations: tuple[Participation, ...]


def would_create_cycle(
    links: list[Link], parent: str, child: str, start: date, until: date | None
) -> bool:
    """True if ``parent`` is already a (period-overlapping) descendant of ``child``."""
    by_parent: dict[str, list[str]] = {}
    for link in links:
        if link.overlaps(start, until):
            by_parent.setdefault(link.parent, []).append(link.child)
    seen = {child}
    stack = [child]
    while stack:
        node = stack.pop()
        if node == parent:
            return True
        for nxt in by_parent.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
                if len(seen) > MAX_MEMBERS * 4:
                    raise ValidationFailure("ownership graph too large to check")
    return False


def max_concurrent_percent(links_to_child: list[Link], new: Link) -> Decimal:
    """Highest total held in the child on any single day of the new link's period."""
    pool = [*links_to_child, new]
    horizon = new.effective_until or date.max
    days = {new.effective_from}
    days.update(
        link.effective_from for link in pool if new.effective_from <= link.effective_from <= horizon
    )
    best = Decimal(0)
    for day in days:
        total = sum((link.percent for link in pool if link.active_on(day)), Decimal(0))
        best = max(best, total)
    return best


def controlled_group(links: list[Link], root: str, on: date) -> GroupView:
    """Everything ``root`` controls on ``on`` (directly or through controlled entities)."""
    children: dict[str, list[Link]] = {}
    for link in links:
        if link.active_on(on):
            children.setdefault(link.parent, []).append(link)
    members: list[Member] = []
    participations: list[Participation] = []
    seen = {root}
    frontier: list[tuple[str, int, Decimal]] = [(root, 0, _HUNDRED)]
    while frontier:
        node, depth, cumulative = frontier.pop(0)
        for link in sorted(children.get(node, ()), key=lambda lk: lk.link_id):
            if not link.controlling:
                participations.append(Participation(node, link.child, link.percent))
                continue
            if link.child in seen:
                continue
            if depth + 1 > MAX_DEPTH or len(members) >= MAX_MEMBERS:
                raise ValidationFailure("group structure too deep or too large")
            seen.add(link.child)
            share = cumulative if link.kind == "branch" else cumulative * link.percent / _HUNDRED
            share = share.quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
            members.append(Member(link.child, depth + 1, node, link.percent, share))
            frontier.append((link.child, depth + 1, share))
    return GroupView(root, tuple(members), tuple(participations))


def ultimate_parent(links: list[Link], lei: str, on: date) -> str:
    """Climb controlling links to the top of the group (the entity itself if none)."""
    parents: dict[str, list[Link]] = {}
    for link in links:
        if link.active_on(on) and link.controlling:
            parents.setdefault(link.child, []).append(link)
    current, seen = lei, {lei}
    for _ in range(MAX_DEPTH):
        options = parents.get(current)
        if not options:
            return current
        nxt = max(options, key=lambda lk: (lk.percent, -lk.link_id)).parent
        if nxt in seen:  # defensive: a cycle should be impossible, never loop forever
            return current
        seen.add(nxt)
        current = nxt
    return current
