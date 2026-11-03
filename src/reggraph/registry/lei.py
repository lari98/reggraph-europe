"""Legal Entity Identifier (ISO 17442) validation.

A LEI is 20 characters: 18 letters/digits followed by two check digits. The check is the
ISO 7064 MOD 97-10 rule: turn letters into numbers (A=10 ... Z=35), read the result as one
integer; it must leave remainder 1 when divided by 97. This catches typing mistakes before
a wrong identifier can reach the registry.
"""

from __future__ import annotations

import re
from typing import Final

from reggraph.core.errors import ValidationFailure

_LEI_RE: Final = re.compile(r"[A-Z0-9]{18}[0-9]{2}")


def _checksum_ok(lei: str) -> bool:
    digits = "".join(str(int(ch, 36)) for ch in lei)
    return int(digits) % 97 == 1


def validate_lei(value: str) -> str:
    """Return the LEI in upper case, or raise ``ValidationFailure``."""
    if not isinstance(value, str) or len(value) != 20:
        raise ValidationFailure("LEI must be exactly 20 characters")
    lei = value.upper()
    if not _LEI_RE.fullmatch(lei) or not _checksum_ok(lei):
        raise ValidationFailure("LEI is malformed or its check digits are wrong")
    return lei
