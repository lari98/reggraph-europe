# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/); every
module also has its own version in `module_versions.json`. Format follows
[Keep a Changelog](https://keepachangelog.com/).

## Module history

Newest first. Written automatically by `python tools/module_manifest.py bump ...`.

<!-- module-log -->
- **app-shell 0.3.0** (2026-11-03, minor): Release 0.3.0 (Phase 3)
- **api 0.3.0** (2026-11-03, minor): Thirteen exposure, insurance and connectivity routes; bounded path integers (fixes 500 on huge ids)
- **registry 0.2.0** (2026-11-03, minor): Shared Ref type; existing_leis, links_on, ultimate_parent_of helpers for graph modules
- **storage 0.3.0** (2026-11-03, minor): Migration 3: exposure batches/positions and insurance contract versions with immutability triggers
- **security 0.3.0** (2026-11-03, minor): Add exposure and insurance read/write permissions (analyst, compliance officer, tenant admin)
- **currency 0.2.0** (2026-11-03, minor): Add parse_money_amount: strict amount parsing for API input
- **connectivity 0.1.0** (2026-11-03, new): Phase 3: insurance profile, interconnection (counterparty and insurer) and ecosystem graph with lineage
- **insurance 0.1.0** (2026-11-03, new): Phase 3: versioned insurer-to-insured contracts, reinsurance links, audited
- **exposure 0.1.0** (2026-11-03, new): Phase 3: immutable exposure batches and positions, lineage, converted edges, concentration (HHI), neighbourhood, reproducibility hash
- **app-shell 0.2.0** (2026-11-03, minor): Release 0.2.0 (Phase 2)
- **api 0.2.0** (2026-11-03, minor): Ten registry routes, entity and end-link request schemas, registry in the service container
- **storage 0.2.0** (2026-11-03, minor): Migration 2: entity_versions and ownership_links with immutability triggers
- **security 0.2.0** (2026-11-03, minor): Add registry:read and registry:write permissions to the role matrix
- **registry 0.1.0** (2026-11-03, new): Phase 2: LEI-keyed entities, licences, ownership links, groups, entity-to-rule mapping

## [0.3.0] - Phase 3: exposure graph and insurance connectivity

### Added
- Exposure batches and positions: immutable, content-hashed, with source system and loader;
  duplicate loads refused; both parties must be registered in the tenant.
- Exposure queries in any supported currency: edges, concentration (shares, HHI, top-1/top-3,
  by counterparty or by ownership group) and neighbourhood, each with lineage (batches, exchange
  rates) and a reproducibility hash; `max_batch` reproduces earlier answers.
- Insurance contracts: versioned, audited, reinsurance links, insurer type check.
- Connectivity views: cover profile, interconnection (counterparty and insurer at once) and an
  ecosystem graph with exposure, insurance and ownership edges.
- Permissions `exposure:read|write`, `insurance:read|write` (analyst, compliance officer, tenant
  admin). Migration 3. Thirteen API routes. 129 new tests.
- `docs/EXPOSURE_AND_INSURANCE.md`.

### Fixed
- A path integer beyond SQLite's range (for example `/v1/ownership/99999999999999999999/end`)
  returned HTTP 500; it is now a 422. Found by the new attacker tests.
- Neighbourhood edge order depended on input order; now sorted, so results are deterministic.

## [0.2.0] - Phase 2: registry

### Added
- Registry of banks, insurers and corporates keyed by LEI (ISO 17442 checksum verified), with
  versioned history, licences (type, authority, country, validity) and reporting currency / basis.
- Ownership links between entities (subsidiary, participation, branch) with periods; refuses
  cycles, duplicates and more than 100 % held in one child on any day.
- Indicative control perimeter ("group") on any date with effective percentages, ultimate parent,
  and entity-to-rule mapping (home country + valid licences, individual / consolidated basis).
- Permissions `registry:read` and `registry:write`; ten new API routes; migration 2 with
  database triggers that keep entity history and ownership links immutable.
- 96 new tests (267 in total) including cross-tenant, id-guessing, cycle-bomb, deep-chain and percentage attacks.
- `tools/module_manifest.py add` to register a new module.

### Fixed
- Percentages in ownership links no longer accept exponent notation or JSON floats.

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
