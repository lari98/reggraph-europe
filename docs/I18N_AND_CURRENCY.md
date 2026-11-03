# Languages and currencies

## Languages (34 locale tags, 28 catalogs)

| Group | Tags |
|---|---|
| EU official (24) | bg hr cs da nl en et fi fr de el hu ga it lv lt mt pl pt ro sk sl es sv |
| Switzerland | de-CH fr-CH it-CH rm |
| United States / United Kingdom | en-US en-GB |
| Singapore | en-SG zh-SG ms-SG ta-SG |

Regional tags share the catalog of their language (`de-CH` uses `de`; `zh-SG` uses `zh`).

* **Negotiation:** `Accept-Language` is parsed with an allowlist; anything unknown or malformed
  falls back to English. The chosen tag is returned in `Content-Language`.
* **Fallback:** a missing key falls back to English, never to an empty string.
* **Quality:** catalogs are **machine drafts**. Each file carries `_meta` with the note
  "machine-draft: needs native-speaker review". Do not ship to customers before review.
* **Add a language:** add the tag in `i18n/locales.py`, create `catalogs/<lang>.json` with all
  15 keys, run `pytest tests/unit/test_jurisdictions_i18n.py` (it fails if a key is missing).
* **Rules text vs UI text:** regulation titles can carry per-language titles (`title_i18n`);
  the legally binding text stays in the source language with its official citation.

## Currencies

| Group | Codes | Note |
|---|---|---|
| Euro area (21 members) | EUR | Bulgaria joined on 2026-01-01 |
| EU members **not** using the euro | CZK DKK HUF PLN RON SEK | each jurisdiction knows its own currency |
| Others | CHF USD GBP SGD | |
| Legacy (history only) | BGN (1.95583 per EUR), HRK (7.53450 per EUR) | fixed conversion rates; cannot be quoted |

* `GET /v1/jurisdictions` returns `currency` and `euro_area` for each country so a client
  never has to guess.
* **Conversion** goes through EUR (`A → EUR → B`). The result reports the rate, the rate date
  and the source. If either rate is older than five days, conversion is **refused** rather than
  guessed.
* **Rounding:** half-even to the target currency's minor units; intermediate maths keeps full
  precision.
* **Display:** `format_money(money, locale)` applies the locale's grouping, decimal mark and
  symbol position (for example `1 234,50 Kč`, `CHF 1’234.50`, `€1,234.50`). A foreign currency
  shows an unambiguous symbol (`S$` abroad, `$` in Singapore).
* **Rates:** tenants load daily rates with `PUT /v1/fx-rates` (permission `fx:write`). Rates
  are insert-only so any past conversion can be reproduced.
