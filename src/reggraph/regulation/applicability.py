"""Which rules apply to a legal entity on a given date? (point-in-time selection)

Selection rules
---------------
====  =================================================================================
A1    Only approved versions count (a superseded version still counts for dates before
      its successor applies). Drafts, rejected and withdrawn versions never apply.
A2    The jurisdiction must be inside the rule's scope (``EU`` expands to all 27 members).
A3    The entity type must be listed on the rule.
A4    A rule written for ``both`` bases matches any basis; otherwise the basis must match.
A5    The date must be on or after the rule's *application* date.
A6    Per rule key, the highest version number that satisfies A1-A5 wins.
A7    ``in_transition`` is true while the date is on or before ``transition_end``.
====  =================================================================================

"Withdrawn" means the record was removed as wrong or void. A real repeal is entered as a new
version with new dates, so history stays truthful.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from reggraph.regulation.models import (
    ConsolidationBasis,
    EntityType,
    RuleStatus,
    RuleVersion,
)

_ACTIVE = {RuleStatus.APPROVED, RuleStatus.SUPERSEDED}


@dataclass(frozen=True, slots=True)
class ApplicableRule:
    version: RuleVersion
    in_transition: bool


def select_applicable(
    versions: Iterable[RuleVersion],
    jurisdiction: str,
    entity_type: EntityType,
    basis: ConsolidationBasis,
    on: date,
) -> list[ApplicableRule]:
    best: dict[str, RuleVersion] = {}
    for version in versions:
        rule = version.rule
        if version.status not in _ACTIVE:
            continue
        if jurisdiction not in rule.scope_codes():
            continue
        if entity_type not in rule.entity_types:
            continue
        if rule.consolidation is not ConsolidationBasis.BOTH and rule.consolidation is not basis:
            continue
        if on < rule.application_date:
            continue
        current = best.get(version.rule_key)
        if current is None or version.version_no > current.version_no:
            best[version.rule_key] = version
    result = [
        ApplicableRule(v, v.rule.transition_end is not None and on <= v.rule.transition_end)
        for v in best.values()
    ]
    return sorted(result, key=lambda item: item.version.rule_key)
