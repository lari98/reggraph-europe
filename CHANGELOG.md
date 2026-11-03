# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/); every
module also has its own version in `module_versions.json`. Format follows
[Keep a Changelog](https://keepachangelog.com/).

## Module history

Newest first. Written automatically by `python tools/module_manifest.py bump ...`.

<!-- module-log -->

## [0.1.0] - Phase 1: foundation

### Added
- Jurisdiction registry: EU-27 plus Switzerland, United States, United Kingdom and Singapore,
  with the euro-area / non-euro split (CZ, DK, HU, PL, RO, SE keep their own currencies).
- Currency: `Decimal`-only `Money`, EUR-triangulated conversion with stale-rate refusal,
  locale-aware formatting for 11 live currencies.
- Languages: 24 EU official languages, Swiss, US, UK and Singapore locales (34 tags,
  28 catalogs, flagged for native-speaker review).
- Regulation store: immutable versions, four-eyes approval, ingestion as draft only,
  point-in-time applicability.
- Security: signed tokens with key rotation, role matrix, per-tenant tamper-evident audit chain,
  rate limiting, input hygiene, hardened HTTP layer.
- Tests: unit, integration and attacker-style security tests; ruff, mypy (strict), bandit.
