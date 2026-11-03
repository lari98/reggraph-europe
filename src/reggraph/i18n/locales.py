"""Supported locales and Accept-Language negotiation.

Coverage (34 tags)::

    EU official languages (24) : bg hr cs da nl en et fi fr de el hu ga it lv lt mt pl pt
                                 ro sk sl es sv
    Switzerland (4)            : de-CH fr-CH it-CH rm
    United States / UK (2)     : en-US en-GB
    Singapore (4)              : en-SG zh-SG (Simplified Chinese) ms-SG (Malay) ta-SG (Tamil)

Security notes
--------------
* A locale tag from a client is only ever *compared* against this allowlist. It is never used
  to build a file path or a query (no path traversal).
* The header is length-capped and entry-capped before parsing (no parser abuse).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Locale:
    tag: str
    language: str
    native_name: str
    group: str  # eu | ch | us | uk | sg


def _loc(tag: str, native: str, group: str) -> Locale:
    return Locale(tag, tag.split("-")[0], native, group)


SUPPORTED_LOCALES: Final[tuple[Locale, ...]] = (
    _loc("bg", "Български", "eu"),
    _loc("hr", "Hrvatski", "eu"),
    _loc("cs", "Čeština", "eu"),
    _loc("da", "Dansk", "eu"),
    _loc("nl", "Nederlands", "eu"),
    _loc("en", "English", "eu"),
    _loc("et", "Eesti", "eu"),
    _loc("fi", "Suomi", "eu"),
    _loc("fr", "Français", "eu"),
    _loc("de", "Deutsch", "eu"),
    _loc("el", "Ελληνικά", "eu"),
    _loc("hu", "Magyar", "eu"),
    _loc("ga", "Gaeilge", "eu"),
    _loc("it", "Italiano", "eu"),
    _loc("lv", "Latviešu", "eu"),
    _loc("lt", "Lietuvių", "eu"),
    _loc("mt", "Malti", "eu"),
    _loc("pl", "Polski", "eu"),
    _loc("pt", "Português", "eu"),
    _loc("ro", "Română", "eu"),
    _loc("sk", "Slovenčina", "eu"),
    _loc("sl", "Slovenščina", "eu"),
    _loc("es", "Español", "eu"),
    _loc("sv", "Svenska", "eu"),
    _loc("de-CH", "Deutsch (Schweiz)", "ch"),
    _loc("fr-CH", "Français (Suisse)", "ch"),
    _loc("it-CH", "Italiano (Svizzera)", "ch"),
    _loc("rm", "Rumantsch", "ch"),
    _loc("en-US", "English (United States)", "us"),
    _loc("en-GB", "English (United Kingdom)", "uk"),
    _loc("en-SG", "English (Singapore)", "sg"),
    _loc("zh-SG", "简体中文（新加坡）", "sg"),
    _loc("ms-SG", "Bahasa Melayu (Singapura)", "sg"),
    _loc("ta-SG", "தமிழ் (சிங்கப்பூர்)", "sg"),
)

_BY_TAG: Final[dict[str, Locale]] = {loc.tag: loc for loc in SUPPORTED_LOCALES}
DEFAULT_LOCALE: Final = "en"

_TAG_RE: Final = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}$")
_MAX_HEADER_LEN: Final = 1024
_MAX_ENTRIES: Final = 20


def is_supported(tag: str) -> bool:
    return tag in _BY_TAG


def catalog_stem(tag: str) -> str:
    """Name of the catalog file (without .json) that holds the strings for ``tag``."""
    return tag.split("-")[0]


def _normalise(tag: str) -> str:
    parts = tag.split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        out.append(part.upper() if len(part) == 2 else part.title())
    return "-".join(out)


def _match(tag: str) -> str | None:
    """Best supported tag for one requested tag, or None."""
    norm = _normalise(tag)
    if norm in _BY_TAG:
        return norm
    language = norm.split("-")[0]
    if language in _BY_TAG:  # for example de-AT -> de, fr-BE -> fr
        return language
    for loc in SUPPORTED_LOCALES:  # for example zh-CN -> zh-SG, ta-IN -> ta-SG
        if loc.language == language:
            return loc.tag
    return None


def negotiate(header: str | None, default: str = DEFAULT_LOCALE) -> str:
    """Pick the best supported locale from an ``Accept-Language`` header. Never raises."""
    if not header:
        return default
    ranked: list[tuple[float, int, str]] = []
    for index, raw in enumerate(header[:_MAX_HEADER_LEN].split(",")[:_MAX_ENTRIES]):
        piece, _, params = raw.strip().partition(";")
        tag = piece.strip()
        if tag == "*" or not _TAG_RE.match(tag):
            continue
        quality = 1.0
        params = params.strip().lower()
        if params.startswith("q="):
            try:
                quality = min(1.0, max(0.0, float(params[2:])))
            except ValueError:
                continue
        if quality > 0:
            ranked.append((-quality, index, tag))
    for _, _, tag in sorted(ranked):
        found = _match(tag)
        if found:
            return found
    return default
