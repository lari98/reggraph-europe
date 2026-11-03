"""Jurisdictions, locale negotiation and translation catalogs (modules: jurisdictions, i18n)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reggraph.i18n import SUPPORTED_LOCALES, Translator, negotiate
from reggraph.jurisdictions import (
    eu_members,
    euro_area,
    expand_scope,
    get_jurisdiction,
    non_euro_eu,
)

EU_LANGS = {
    "bg",
    "hr",
    "cs",
    "da",
    "nl",
    "en",
    "et",
    "fi",
    "fr",
    "de",
    "el",
    "hu",
    "ga",
    "it",
    "lv",
    "lt",
    "mt",
    "pl",
    "pt",
    "ro",
    "sk",
    "sl",
    "es",
    "sv",
}


def test_eu_has_27_members() -> None:
    assert len(eu_members()) == 27


def test_euro_split() -> None:
    assert {j.code for j in non_euro_eu()} == {"CZ", "DK", "HU", "PL", "RO", "SE"}
    assert len(euro_area()) == 21
    assert get_jurisdiction("BG") is not None and get_jurisdiction("BG").euro_area  # type: ignore[union-attr]


def test_uk_alias_and_non_eu_markets() -> None:
    assert get_jurisdiction("UK").code == "GB"  # type: ignore[union-attr]
    for code in ("CH", "US", "SG"):
        assert get_jurisdiction(code) is not None


def test_expand_scope() -> None:
    assert len(expand_scope(["EU"])) == 27
    assert expand_scope(["EU", "CH"]) >= {"DE", "CH"}
    with pytest.raises(ValueError):
        expand_scope(["ZZ"])


def test_all_24_eu_languages_present() -> None:
    langs = {loc.language for loc in SUPPORTED_LOCALES}
    assert langs >= EU_LANGS
    assert {"zh", "ms", "ta"} <= langs  # Singapore
    tags = {loc.tag for loc in SUPPORTED_LOCALES}
    assert {"en-US", "en-GB", "en-SG"} <= tags | {"en"}


def test_every_locale_has_a_catalog_with_every_key() -> None:
    translator = Translator()
    keys = translator.keys()
    assert len(keys) == 15
    for loc in SUPPORTED_LOCALES:
        for key in keys:
            text = translator.translate(key, loc.tag)
            assert text and "$" not in text, (loc.tag, key)


def test_catalog_files_are_valid_json_with_review_marker() -> None:
    directory = Path(__file__).resolve().parents[2] / "src/reggraph/i18n/catalogs"
    files = sorted(directory.glob("*.json"))
    assert len(files) == 28
    for file in files:
        data = json.loads(file.read_text(encoding="utf-8"))
        assert "_meta" in data, file.name


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("de-CH,de;q=0.8,en;q=0.5", "de-CH"),
        ("fr", "fr"),
        ("xx-YY", "en"),
        ("", "en"),
        (None, "en"),
        ("../../etc/passwd", "en"),
        ("en;q=0," * 5, "en"),
    ],
)
def test_negotiate(header: str | None, expected: str) -> None:
    assert negotiate(header) == expected


def test_translator_falls_back_to_english_for_unknown_locale() -> None:
    translator = Translator()
    assert translator.translate("status.ok", "zz") == translator.translate("status.ok", "en")
