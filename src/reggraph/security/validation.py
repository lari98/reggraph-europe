"""Input hygiene shared by the API and the domain layer.

Rules
-----
====  ==============================================================================
Rule  What is blocked, and why
====  ==============================================================================
V1    Control characters (NUL, ESC, CR ...): log injection, truncation tricks.
V2    Bidirectional override characters (U+202A-202E, U+2066-2069, U+200E/F):
      "Trojan Source" style text that displays differently from what it is.
V3    Zero-width characters and BOM (U+200B, U+2060, U+FEFF): invisible look-alike text.
V4    Surrogates and private-use code points: not valid text.
V5    Over-long input: every text field has an explicit maximum length.
V6    Text is normalised to Unicode NFC so equal-looking strings compare equal.
====  ==============================================================================

Legitimate text in all supported scripts (Latin, Greek, Cyrillic, Chinese, Tamil ...) passes.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

from reggraph.core.errors import ValidationFailure

IDENT_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}$")
RULE_KEY_RE: Final = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,63}$")

# Zero-width, bidi marks/embeddings/isolates, word joiner, BOM (written as escapes on purpose).
_BLOCKED_CHARS: Final = frozenset(
    "\u200b\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\u2066\u2067\u2068\u2069\ufeff"
)


def ensure_identifier(value: str, what: str = "identifier") -> str:
    """Tenant ids, user ids and similar. Letters, digits and ``. _ : @ -``; max 64 chars."""
    if not isinstance(value, str) or not IDENT_RE.match(value):
        raise ValidationFailure(f"invalid {what}")
    return value


def ensure_rule_key(value: str) -> str:
    """Stable rule identifier such as ``EU.CRR.ART92``."""
    if not isinstance(value, str) or not RULE_KEY_RE.match(value):
        raise ValidationFailure("invalid rule key")
    return value


def clean_text(
    value: str,
    *,
    max_len: int = 500,
    multiline: bool = False,
    allow_empty: bool = False,
) -> str:
    """Validate and normalise free text. Raises ``ValidationFailure`` on any violation."""
    if not isinstance(value, str):
        raise ValidationFailure("text must be a string")
    if len(value) > max_len * 4:  # cheap guard before normalising huge input
        raise ValidationFailure("text too long")
    text = unicodedata.normalize("NFC", value).strip()
    if len(text) > max_len:
        raise ValidationFailure("text too long")
    if not text and not allow_empty:
        raise ValidationFailure("text must not be empty")
    for char in text:
        category = unicodedata.category(char)
        if char in _BLOCKED_CHARS or category in {"Cs", "Co"}:
            raise ValidationFailure("text contains forbidden characters")
        if category == "Cc" and not (multiline and char in "\n\t"):
            raise ValidationFailure("text contains control characters")
    return text
