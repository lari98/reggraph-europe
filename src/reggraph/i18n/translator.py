"""Message catalogs and lookup.

Lookup order for a tag such as ``de-CH``: ``de`` catalog (the catalog stem), then ``en``.

Catalog files live in ``catalogs/<stem>.json``::

    { "_meta": {"review_status": "..."}, "status.ok": "...", ... }

Security notes
--------------
* Catalog files are trusted repository content, loaded once at start-up.
* Placeholders use ``string.Template`` (``$name``) with ``safe_substitute``. Unlike
  ``str.format`` it cannot read attributes (``{0.__class__}``), so a hostile parameter value
  or template cannot leak internals.
"""

from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Final

from reggraph.i18n.locales import DEFAULT_LOCALE, SUPPORTED_LOCALES, catalog_stem

_DEFAULT_DIR: Final = Path(__file__).parent / "catalogs"
META_KEY: Final = "_meta"


class Translator:
    def __init__(self, catalog_dir: Path | None = None) -> None:
        directory = catalog_dir or _DEFAULT_DIR
        self._catalogs: dict[str, dict[str, str]] = {}
        # Only stems derived from the allowlist are ever opened.
        for stem in sorted({catalog_stem(loc.tag) for loc in SUPPORTED_LOCALES}):
            path = directory / f"{stem}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self._catalogs[stem] = {k: v for k, v in data.items() if k != META_KEY}

    def keys(self) -> frozenset[str]:
        return frozenset(self._catalogs[DEFAULT_LOCALE])

    def stems(self) -> frozenset[str]:
        return frozenset(self._catalogs)

    def catalog(self, stem: str) -> dict[str, str]:
        return dict(self._catalogs[stem])

    def translate(self, key: str, locale: str = DEFAULT_LOCALE, **params: str) -> str:
        """Return the message for ``key``. Unknown keys return the key itself (never raise)."""
        stem = catalog_stem(locale)
        text = self._catalogs.get(stem, {}).get(key) or self._catalogs[DEFAULT_LOCALE].get(key)
        if text is None:
            return key
        return Template(text).safe_substitute(params) if params else text
