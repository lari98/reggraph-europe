"""Jurisdiction registry: EU-27, Switzerland, United States, United Kingdom, Singapore."""

from reggraph.jurisdictions.registry import (
    Jurisdiction,
    all_jurisdictions,
    eu_members,
    euro_area,
    expand_scope,
    get_jurisdiction,
    non_euro_eu,
)

__all__ = [
    "Jurisdiction",
    "all_jurisdictions",
    "eu_members",
    "euro_area",
    "expand_scope",
    "get_jurisdiction",
    "non_euro_eu",
]
