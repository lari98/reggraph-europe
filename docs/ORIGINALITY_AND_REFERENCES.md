# Originality and references

## Statement

All source code in this repository was written for this project against the requirements in the
*RegGraph Europe – Pitch Pack*. No code, tests, schemas or translations were copied from another
project. Standards, laws and published ideas were **consulted** as references (below); where an
idea is used, the implementation is our own.

## Concepts consulted (ideas, not code)

| Area | Reference | Used for |
|---|---|---|
| Currency codes and minor units | ISO 4217 | `currency/codes.py` |
| Euro reference rates | European Central Bank, euro foreign exchange reference rates | triangulation through EUR; rate-feed design |
| Euro area membership | European Central Bank / Council of the EU, euro area enlargement pages | euro vs non-euro split in `jurisdictions/` |
| Language tags | BCP 47 (RFC 5646); HTTP `Accept-Language` (RFC 9110 §12.5.4) | `i18n/locales.py` negotiation |
| Message authentication | HMAC (RFC 2104, FIPS 198-1) | token signatures |
| Tamper-evident logging | B. Schneier and J. Kelsey, "Secure Audit Logs to Support Computer Forensics" (1999) | hash-chained audit log |
| Web / API security | OWASP ASVS; OWASP API Security Top 10 | threat list, headers, validation rules |
| Trojan-source attacks | Boucher and Anderson, "Trojan Source: Invisible Vulnerabilities" (2021) | bidi / invisible character blocking |
| Secure development | NIST SP 800-218 (SSDF) | pipeline gates |
| Decimal arithmetic | Python `decimal` documentation | exact money maths and rounding |
| Versioning and changelog | Semantic Versioning 2.0.0; Keep a Changelog | module versioning |
| Regulatory sources | EUR-Lex (official EU law), FINMA, FCA, SEC, MAS | the rule register in the Pitch Pack |

The full citation list for the regulatory content (R1–R17) is in the Pitch Pack document.

## Third-party software (installed as dependencies, not copied)

| Package | Licence | Role |
|---|---|---|
| FastAPI, Starlette | MIT, BSD-3 | HTTP framework |
| Pydantic | MIT | validation |
| Uvicorn | BSD-3 | server |
| pytest, pytest-cov, Hypothesis, httpx | MIT / MPL-2.0 / BSD-3 | testing |
| ruff, mypy, bandit, pip-audit | MIT / Apache-2.0 | quality and security gates |

Re-check licences before commercial distribution; `pip-audit` covers vulnerabilities, not licences.

## Phase 5 references

* ECB, *euro foreign exchange reference rates* (published XML layout) — read to write the parser.
* OpenID Connect Core 1.0; RFC 7517 (JWK), RFC 7519 (JWT), RFC 8725 (JWT best current practice).
* OWASP SSRF Prevention and XML External Entity Prevention cheat sheets.
* Libraries used as dependencies, not copied: PyJWT (MIT), defusedxml (PSF).
* All Phase 5 code is original.

## Honest caveats

* Translations were produced as drafts and are labelled for native-speaker review.
* Euro-area membership and rates change; verify against the ECB before relying on them.
* Regulatory content is decision support, not legal advice.
