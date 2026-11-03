"""ECB and EUR-Lex parsers (module: connectors)."""

from __future__ import annotations

from datetime import date

import pytest

from reggraph.connectors import parse_ecb_rates, parse_feed
from reggraph.netio import UpstreamError
from tests.phase5 import celex_link, ecb_xml, rss


def test_ecb_file_is_parsed_as_text_decimals() -> None:
    day, rates = parse_ecb_rates(ecb_xml("2026-03-02", {"USD": "1.0845", "JPY": "162.31"}))
    assert day == date(2026, 3, 2)
    assert rates == {"USD": "1.0845", "JPY": "162.31"}


@pytest.mark.parametrize(
    "document",
    [
        b"",
        b"not xml",
        b"<a><b></a>",
        ecb_xml("2026-13-45"),  # not a date
        ecb_xml("02/03/2026"),
        ecb_xml(rates={}),  # no rates
        ecb_xml(rates={"usd": "1.2"}),  # lower-case code
        ecb_xml(rates={"US": "1.2"}),
        ecb_xml(rates={"USD": "1e3"}),  # exponent
        ecb_xml(rates={"USD": "NaN"}),
        ecb_xml(rates={"USD": "-1.2"}),
        ecb_xml(rates={"USD": "0"}),
        ecb_xml(rates={"USD": "0.0000"}),
        ecb_xml(rates={"USD": "1,25"}),
        ecb_xml(rates={"USD": " 1.25"}),
        ecb_xml(rates={"USD": "\u0661.\u0662\u0665"}),  # non-ASCII digits
        ecb_xml(rates={f"A{chr(65 + i // 26)}{chr(65 + i % 26)}": "1.5" for i in range(81)}),
        # two dated blocks
        ecb_xml().replace(
            b"</Cube></Cube></gesmes", b'</Cube><Cube time="2026-03-01"></Cube></Cube></gesmes'
        ),
        # duplicate currency
        ecb_xml().replace(b'currency="PLN"', b'currency="USD"'),
    ],
)
def test_ecb_bad_files_are_refused(document: bytes) -> None:
    with pytest.raises(UpstreamError):
        parse_ecb_rates(document)


def test_ecb_xml_attacks_are_refused() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><Envelope><Cube><Cube time="2026-03-02">'
        b'<Cube currency="USD" rate="1.2"/></Cube></Cube><x>&b;</x></Envelope>'
    )
    xxe = (
        b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        b"<Envelope>&x;</Envelope>"
    )
    for payload in (bomb, xxe):
        with pytest.raises(UpstreamError):
            parse_ecb_rates(payload)


def test_feed_items_are_keyed_by_celex() -> None:
    items, skipped = parse_feed(
        rss(
            (
                "Regulation (EU) 2024/1623",
                celex_link("32024R1623"),
                "Tue, 19 Jun 2024 09:00:00 GMT",
            ),
            ("Directive (EU) 2024/1619", celex_link("32024L1619"), "2024-06-19T10:00:00Z"),
            ("No date", celex_link("32024R0001"), "garbage"),
        )
    )
    assert skipped == 0
    assert [i.key for i in items] == ["celex:32024R1623", "celex:32024L1619", "celex:32024R0001"]
    assert [i.published for i in items] == ["2024-06-19", "2024-06-19", None]


def test_atom_feed_is_understood() -> None:
    atom = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Act</title>'
        f'<link href="{celex_link("32025R0042")}"/><updated>2025-01-02T00:00:00Z</updated>'
        "</entry></feed>"
    ).encode()
    items, _ = parse_feed(atom)
    assert items[0].key == "celex:32025R0042" and items[0].published == "2025-01-02"


def test_feed_without_celex_uses_a_hash_and_duplicates_collapse() -> None:
    link = "https://eur-lex.europa.eu/eli/reg/2024/1623/oj"
    items, skipped = parse_feed(rss(("A", link, ""), ("A again", link, "")))
    assert len(items) == 1 and items[0].key.startswith("url:") and skipped == 1


@pytest.mark.parametrize(
    "link",
    [
        "https://evil.example/legal-content?uri=CELEX:32024R1623",  # other host
        "http://eur-lex.europa.eu/x",  # not https
        "https://eur-lex.europa.eu.evil.example/x",
        "https://user:pw@eur-lex.europa.eu/x",
        "https://eur-lex.europa.eu:8443/x",
        "javascript:alert(1)",
        "",
    ],
)
def test_feed_links_to_other_places_are_dropped(link: str) -> None:
    items, skipped = parse_feed(rss(("Title", link.replace("&", "&amp;"), "")))
    assert items == [] and skipped == 1


@pytest.mark.parametrize("title", ["", "x" * 400, "a\u0085b", "bidi ‮ text", "zero​width"])
def test_feed_titles_are_cleaned_or_item_skipped(title: str) -> None:
    items, skipped = parse_feed(rss((title, celex_link("32024R1623"), "")))
    assert items == [] and skipped == 1


def test_feed_is_capped_and_bad_xml_refused() -> None:
    many = rss(*[(f"t{i}", celex_link(f"32024R{i:04d}"), "") for i in range(250)])
    items, skipped = parse_feed(many)
    assert len(items) == 200 and skipped == 50
    for bad in (b"", b"<rss>", b"<!DOCTYPE x [<!ENTITY e 'e'>]><rss>&e;</rss>"):
        with pytest.raises(UpstreamError):
            parse_feed(bad)


def test_markup_in_titles_stays_text() -> None:
    items, _ = parse_feed(
        rss(("&lt;script&gt;alert(1)&lt;/script&gt;", celex_link("32024R1623"), ""))
    )
    assert items[0].title == "<script>alert(1)</script>"  # data only; the page uses textContent
