"""Static reference data about the jurisdictions the platform understands.

Source for the country list and the euro-area split: Pitch Pack, section 2
("EU member-state coverage (2026)"): 27 EU members, 21 in the euro area
(Bulgaria joined on 1 January 2026), six outside it (Czechia, Denmark, Hungary,
Poland, Romania, Sweden). Switzerland is neither EU nor EEA.

``languages`` lists only locales the platform ships catalogs for. A country can
have more official languages than listed (for example Luxembourgish); those are
added in a later phase (see docs/I18N_AND_CURRENCY.md).

Group    Meaning
-------  --------------------------------------------------------------
EU       European Union member state
CH       Switzerland (not EU, not EEA)
US       United States
UK       United Kingdom (ISO code GB)
SG       Singapore
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Jurisdiction:
    code: str  # ISO 3166-1 alpha-2
    name: str  # English name
    group: str  # EU | CH | US | UK | SG
    currency: str  # ISO 4217 code of the legal tender today
    euro_area: bool
    languages: tuple[str, ...]  # locale tags with shipped catalogs, first = primary


def _eu(code: str, name: str, currency: str, languages: tuple[str, ...]) -> Jurisdiction:
    return Jurisdiction(code, name, "EU", currency, currency == "EUR", languages)


_JURISDICTIONS: Final[tuple[Jurisdiction, ...]] = (
    _eu("AT", "Austria", "EUR", ("de",)),
    _eu("BE", "Belgium", "EUR", ("nl", "fr", "de")),
    _eu("BG", "Bulgaria", "EUR", ("bg",)),  # euro since 2026-01-01
    _eu("HR", "Croatia", "EUR", ("hr",)),
    _eu("CY", "Cyprus", "EUR", ("el",)),
    _eu("CZ", "Czechia", "CZK", ("cs",)),
    _eu("DK", "Denmark", "DKK", ("da",)),
    _eu("EE", "Estonia", "EUR", ("et",)),
    _eu("FI", "Finland", "EUR", ("fi", "sv")),
    _eu("FR", "France", "EUR", ("fr",)),
    _eu("DE", "Germany", "EUR", ("de",)),
    _eu("GR", "Greece", "EUR", ("el",)),
    _eu("HU", "Hungary", "HUF", ("hu",)),
    _eu("IE", "Ireland", "EUR", ("en", "ga")),
    _eu("IT", "Italy", "EUR", ("it",)),
    _eu("LV", "Latvia", "EUR", ("lv",)),
    _eu("LT", "Lithuania", "EUR", ("lt",)),
    _eu("LU", "Luxembourg", "EUR", ("fr", "de")),
    _eu("MT", "Malta", "EUR", ("mt", "en")),
    _eu("NL", "Netherlands", "EUR", ("nl",)),
    _eu("PL", "Poland", "PLN", ("pl",)),
    _eu("PT", "Portugal", "EUR", ("pt",)),
    _eu("RO", "Romania", "RON", ("ro",)),
    _eu("SK", "Slovakia", "EUR", ("sk",)),
    _eu("SI", "Slovenia", "EUR", ("sl",)),
    _eu("ES", "Spain", "EUR", ("es",)),
    _eu("SE", "Sweden", "SEK", ("sv",)),
    Jurisdiction("CH", "Switzerland", "CH", "CHF", False, ("de-CH", "fr-CH", "it-CH", "rm")),
    Jurisdiction("US", "United States", "US", "USD", False, ("en-US",)),
    Jurisdiction("GB", "United Kingdom", "UK", "GBP", False, ("en-GB",)),
    Jurisdiction("SG", "Singapore", "SG", "SGD", False, ("en-SG", "zh-SG", "ms-SG", "ta-SG")),
)

_BY_CODE: Final[dict[str, Jurisdiction]] = {j.code: j for j in _JURISDICTIONS}

# "UK" is not an ISO 3166 code but people type it. Accept it as an alias for GB.
_ALIASES: Final[dict[str, str]] = {"UK": "GB"}

# Scope tokens a rule may use instead of listing every country.
EU_TOKEN: Final = "EU"


def get_jurisdiction(code: str) -> Jurisdiction | None:
    """Look up by ISO code (case-insensitive). Returns None if unknown."""
    key = code.strip().upper()
    return _BY_CODE.get(_ALIASES.get(key, key))


def all_jurisdictions() -> tuple[Jurisdiction, ...]:
    return _JURISDICTIONS


def eu_members() -> tuple[Jurisdiction, ...]:
    return tuple(j for j in _JURISDICTIONS if j.group == "EU")


def euro_area() -> tuple[Jurisdiction, ...]:
    return tuple(j for j in _JURISDICTIONS if j.euro_area)


def non_euro_eu() -> tuple[Jurisdiction, ...]:
    """EU members that do not use the euro (they need their own currency handling)."""
    return tuple(j for j in eu_members() if not j.euro_area)


def expand_scope(tokens: tuple[str, ...]) -> frozenset[str]:
    """Turn scope tokens (country codes or ``EU``) into a set of country codes.

    Unknown tokens raise ``ValueError`` so a typo cannot silently widen or narrow a rule.
    """
    result: set[str] = set()
    for token in tokens:
        upper = token.strip().upper()
        if upper == EU_TOKEN:
            result.update(j.code for j in eu_members())
            continue
        jur = get_jurisdiction(upper)
        if jur is None:
            raise ValueError(f"unknown jurisdiction scope: {token!r}")
        result.add(jur.code)
    return frozenset(result)
