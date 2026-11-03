"""Internationalisation: 24 EU official languages plus Romansh, US/UK English and Singapore."""

from reggraph.i18n.locales import SUPPORTED_LOCALES, Locale, catalog_stem, is_supported, negotiate
from reggraph.i18n.translator import Translator

__all__ = [
    "SUPPORTED_LOCALES",
    "Locale",
    "Translator",
    "catalog_stem",
    "is_supported",
    "negotiate",
]
