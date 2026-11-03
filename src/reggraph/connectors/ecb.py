"""Parser for the ECB euro foreign-exchange reference-rate file (``eurofxref-daily.xml``).

The file is a small XML document::

    <gesmes:Envelope ...><Cube><Cube time="2026-11-02">
        <Cube currency="USD" rate="1.0845"/> ...

Rates are "units of currency per 1 EUR", which is exactly how RegGraph stores them.

=====  =========================================================================
E1     XML is parsed with ``defusedxml``: DTDs, entities and external references
       are refused (no billion-laughs, no XXE).
E2     Exactly one dated block is accepted; the date must be a real calendar date.
E3     Currency codes are three capital ASCII letters; each appears once.
E4     Rates are plain decimal text (no exponent, no float, no NaN) and positive.
E5     At most 80 rates are read; anything larger is rejected, not truncated.
=====  =========================================================================

Reference: the file layout is described on the ECB's public "euro foreign exchange
reference rates" pages. This parser is original code written against that description.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Final

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from reggraph.netio import UpstreamError

MAX_RATES: Final = 80
_RATE_RE: Final = re.compile(r"[0-9]{1,7}(\.[0-9]{1,10})?")
_CODE_RE: Final = re.compile(r"[A-Z]{3}")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ecb_rates(document: bytes) -> tuple[date, dict[str, str]]:
    """Return the reference date and ``{currency: rate_per_eur}`` (text, not floats)."""
    try:
        root = SafeET.fromstring(document, forbid_dtd=True)
    except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
        raise UpstreamError("rate file is not acceptable XML") from exc
    days = [e for e in root.iter() if _local(e.tag) == "Cube" and "time" in e.attrib]
    if len(days) != 1:
        raise UpstreamError("rate file must hold exactly one dated block")
    try:
        day = date.fromisoformat(days[0].attrib["time"])
    except ValueError as exc:
        raise UpstreamError("rate date is not a date") from exc
    rates: dict[str, str] = {}
    for cube in days[0]:
        if _local(cube.tag) != "Cube":
            continue
        code, rate = cube.attrib.get("currency", ""), cube.attrib.get("rate", "")
        if not _CODE_RE.fullmatch(code) or not _RATE_RE.fullmatch(rate) or code in rates:
            raise UpstreamError("rate entry is malformed")
        if Decimal(rate) <= 0:
            raise UpstreamError("rate must be positive")
        rates[code] = rate
        if len(rates) > MAX_RATES:
            raise UpstreamError("too many rates")
    if not rates:
        raise UpstreamError("rate file holds no rates")
    return day, rates
