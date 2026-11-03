"""Parser for an EUR-Lex style RSS 2.0 or Atom feed of newly published legal acts.

The result is a list of *candidates*. Nothing here changes a rule: a person must still open
the item, decide whether it matters and propose a rule (human approval, project principle).

=====  ===========================================================================
R1     XML is parsed with ``defusedxml`` (no DTD, entities or external references).
R2     At most 200 items are read; text fields are cleaned and length-limited.
R3     A link is kept only if it is https on ``eur-lex.europa.eu``: a hostile feed
       cannot plant links to other sites in the dashboard.
R4     The item key is the CELEX number when the link has one, otherwise a hash of the
       link, so the same act is never alerted twice.
R5     Items that fail cleaning are skipped and counted, never half-stored.
=====  ===========================================================================
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from reggraph.core.errors import ValidationFailure
from reggraph.netio import UpstreamError
from reggraph.security.validation import clean_text

MAX_ITEMS: Final = 200
LINK_HOST: Final = "eur-lex.europa.eu"
_CELEX_RE: Final = re.compile(r"CELEX[:=]([0-9][0-9A-Z()_.-]{3,28})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FeedItem:
    key: str
    title: str
    url: str
    published: str | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(node: Any, name: str) -> str:
    for child in node:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _link(node: Any) -> str:
    for child in node:
        if _local(child.tag) == "link":
            return (child.attrib.get("href") or child.text or "").strip()
    return ""


def _published(node: Any) -> str | None:
    raw = (
        _child_text(node, "pubDate")
        or _child_text(node, "updated")
        or _child_text(node, "published")
    )
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            return None


def parse_feed(document: bytes) -> tuple[list[FeedItem], int]:
    """Return ``(items, skipped)``."""
    try:
        root = SafeET.fromstring(document, forbid_dtd=True)
    except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
        raise UpstreamError("feed is not acceptable XML") from exc
    nodes = [e for e in root.iter() if _local(e.tag) in {"item", "entry"}]
    items: list[FeedItem] = []
    skipped = max(0, len(nodes) - MAX_ITEMS)
    seen: set[str] = set()
    for node in nodes[:MAX_ITEMS]:
        link = _link(node)
        try:
            parts = urlsplit(link)
            if parts.scheme != "https" or (parts.hostname or "").lower() != LINK_HOST:
                raise ValidationFailure("link not allowed")
            if parts.username or parts.password or parts.port not in (None, 443):
                raise ValidationFailure("link not allowed")
            title = clean_text(_child_text(node, "title"), max_len=300)
            clean_link = clean_text(link, max_len=500)
        except (ValidationFailure, ValueError):
            skipped += 1
            continue
        celex = _CELEX_RE.search(link)
        key = (
            f"celex:{celex.group(1).upper()}"
            if celex
            else "url:" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]
        )
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        items.append(FeedItem(key, title, clean_link, _published(node)))
    return items, skipped
