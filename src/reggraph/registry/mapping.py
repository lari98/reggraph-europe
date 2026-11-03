"""Entity-to-rule mapping: which approved rules apply to this entity on this day?

Inputs come from the registry (home country, licences in other countries, entity type, whether
it reports individually and/or consolidated and whether it really has subsidiaries) and the
point-in-time rule engine. The result says *why* each rule applies, so a reviewer can check it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from reggraph.registry.store import EntityStore
from reggraph.regulation.models import ConsolidationBasis, RuleVersion
from reggraph.regulation.store import RuleStore
from reggraph.security.rbac import Permission, Principal


@dataclass(slots=True)
class EntityRuleMatch:
    version: RuleVersion
    in_transition: bool
    jurisdictions: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)


def applicable_rules(
    who: Principal, entities: EntityStore, rules: RuleStore, lei: str, on: date
) -> list[EntityRuleMatch]:
    who.require(Permission.REGISTRY_READ)
    who.require(Permission.RULES_READ)
    current = entities.get(who, lei)
    entity = current.entity

    jurisdictions = [entity.jurisdiction]
    for licence in entity.licences:
        if licence.valid_on(on) and licence.jurisdiction not in jurisdictions:
            jurisdictions.append(licence.jurisdiction)

    wants = entity.reporting_basis
    bases: list[ConsolidationBasis] = []
    if wants in (ConsolidationBasis.INDIVIDUAL, ConsolidationBasis.BOTH):
        bases.append(ConsolidationBasis.INDIVIDUAL)
    has_group = entities.has_subsidiaries(who, entity.lei, on)
    if wants in (ConsolidationBasis.CONSOLIDATED, ConsolidationBasis.BOTH) and has_group:
        bases.append(ConsolidationBasis.CONSOLIDATED)
    if not bases:  # asked for consolidated but there is no group: individual is what applies
        bases.append(ConsolidationBasis.INDIVIDUAL)

    merged: dict[tuple[str, int], EntityRuleMatch] = {}
    for jur in jurisdictions:
        for basis in bases:
            for found in rules.applicable(who, jur, entity.entity_type, basis, on):
                key = (found.version.rule_key, found.version.version_no)
                match = merged.setdefault(key, EntityRuleMatch(found.version, found.in_transition))
                if jur not in match.jurisdictions:
                    match.jurisdictions.append(jur)
                if basis.value not in match.bases:
                    match.bases.append(basis.value)
    return sorted(merged.values(), key=lambda m: (m.version.rule_key, m.version.version_no))
