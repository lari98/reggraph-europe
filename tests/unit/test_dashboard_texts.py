"""Console texts (lang.js): every translated language is complete and safe, every key is defined."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from reggraph.dashboard import STATIC_DIR

LANG_JS = Path(STATIC_DIR, "lang.js").read_text(encoding="utf-8")
APP_JS = Path(STATIC_DIR, "app.js").read_text(encoding="utf-8")
INDEX = Path(STATIC_DIR, "index.html").read_text(encoding="utf-8")


def _texts() -> dict[str, dict[str, str]]:
    body = LANG_JS[LANG_JS.index("{", LANG_JS.index("window.RG_I18N")) : LANG_JS.rindex("};") + 1]
    body = re.sub(r"\n  (\w+): \{", r'\n  "\1": {', body)  # language keys
    body = re.sub(r",(\s*[}\]])", r"\1", body)  # trailing commas
    data: dict[str, dict[str, str]] = json.loads(body)
    return data


TEXTS = _texts()
EN = TEXTS["en"]
FULL = ["de", "fr", "it", "es"]


def test_five_languages_are_fully_translated() -> None:
    assert set(TEXTS) == {"en", "de", "fr", "it", "es"}


@pytest.mark.parametrize("lang", FULL)
def test_translation_has_exactly_the_english_keys(lang: str) -> None:
    assert set(TEXTS[lang]) == set(EN), set(EN) ^ set(TEXTS[lang])


@pytest.mark.parametrize("lang", FULL)
def test_placeholders_match_english(lang: str) -> None:
    for key, text in TEXTS[lang].items():
        assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", EN[key])), key


@pytest.mark.parametrize("lang", ["en", *FULL])
def test_texts_are_plain_text_not_markup(lang: str) -> None:
    for key, text in TEXTS[lang].items():
        assert text.strip() and "<" not in text and ">" not in text, (lang, key)
        assert not re.search(r"\b(?:javascript|data):", text, re.I), (lang, key)


@pytest.mark.parametrize("lang", FULL)
def test_translations_differ_from_english_where_it_matters(lang: str) -> None:
    same = [k for k, v in TEXTS[lang].items() if v == EN[k] and len(v) > 12]
    assert len(same) <= 2, same  # almost nothing long was left untranslated


def test_every_key_used_by_the_page_exists() -> None:
    literal = set(re.findall(r"\bt\(\"([a-z0-9.]+)\"", APP_JS)) | set(
        re.findall(r"data-i(?:-ph|-aria|-title)?=\"([a-z0-9.]+)\"", INDEX)
    )
    literal = {k for k in literal if not k.endswith(".")}  # prefixes, checked below
    assert literal and not (literal - set(EN)), literal - set(EN)
    dynamic = (
        [
            f"nav.{n}"
            for n in ("overview", "entities", "exposure", "insurance", "stress", "rules", "data")
        ]
        + [f"ov.h.{n}" for n in ("ok", "warn", "bad")]
        + [f"ov.s.{n}" for n in ("ok", "warn", "bad")]
        + [f"g.{i}{k}" for i in range(1, 6) for k in ("t", "d")]
    )
    assert not (set(dynamic) - set(EN)), set(dynamic) - set(EN)
    words = [
        "bank", "insurer", "reinsurer", "investment_firm", "fund_manager", "corporate", "holding",
        "life", "non_life", "health", "credit_and_surety", "reinsurance",
        "draft", "approved", "rejected", "superseded", "withdrawn", "exposure", "ownership",
        "insurance",
    ]  # fmt: skip
    assert not ({f"w.{w}" for w in words} - set(EN))


def test_page_never_writes_texts_as_markup() -> None:
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in APP_JS and banned not in LANG_JS, banned


def test_page_loads_texts_before_the_app() -> None:
    assert INDEX.index("/dashboard/lang.js") < INDEX.index("/dashboard/app.js")


def test_app_uses_no_browser_storage() -> None:
    for banned in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert banned not in APP_JS, banned
