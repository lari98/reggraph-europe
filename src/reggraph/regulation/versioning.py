"""Compare two versions of a rule and say what changed and whether it matters.

A change is *material* when it can alter a calculation, a legal classification or who the
rule applies to. Material changes are flagged on the review task and in the audit trail.
Every change, material or not, still needs human approval (Pitch Pack, section 6:
"should not silently change binding capital formulas, legal classifications or regulated
operational processes without validation and approval").

Field                    Material?
-----------------------  ---------
numeric_requirements     yes
jurisdictions            yes
entity_types             yes
consolidation            yes
conditions / exemptions  yes
effective/application/transition dates  yes
title, citation, owner, model, data, translations, url  no
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from reggraph.regulation.models import RuleInput

MATERIAL_FIELDS: Final = frozenset(
    {
        "numeric_requirements",
        "jurisdictions",
        "entity_types",
        "consolidation",
        "conditions",
        "exemptions",
        "effective_date",
        "application_date",
        "transition_end",
    }
)


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    old: Any
    new: Any
    material: bool


def diff(old: RuleInput | None, new: RuleInput) -> list[FieldChange]:
    """Fields that differ. A first version (``old`` is None) reports every field as new."""
    new_data = new.model_dump(mode="json")
    old_data = old.model_dump(mode="json") if old else {}
    changes: list[FieldChange] = []
    for name in sorted(new_data):
        if old is None or old_data.get(name) != new_data[name]:
            changes.append(
                FieldChange(name, old_data.get(name), new_data[name], name in MATERIAL_FIELDS)
            )
    return changes


def is_material(changes: list[FieldChange]) -> bool:
    return any(change.material for change in changes)
