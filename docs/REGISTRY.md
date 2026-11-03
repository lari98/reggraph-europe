# Registry (Phase 2): banks, insurers, corporates and their groups

Module `registry/`. Answers four questions: *who is this entity*, *what is it licensed to do and
where*, *who owns it or what does it own*, and *which rules apply to it today*.

## Data model

| Thing | Key | Versioned? | Notes |
|---|---|---|---|
| Entity | LEI (ISO 17442, checksum verified) | Yes — every change is a new immutable version | type, home country, reporting currency, reporting basis, status, licences |
| Licence | inside the entity | with the entity | type, authority, country, reference, valid from / until |
| Ownership link | numeric id per tenant | Insert-only; only an open link may be given an end date | parent → child, percent, kind, from / until |

* **LEI first.** A mistyped identifier is rejected by its two check digits before it reaches the
  database. Lower case is accepted and stored in upper case. Entities without a LEI are out of
  scope for Phase 2.
* **Dissolving** an entity is a new version with `status: dissolved`. Nothing is deleted.
* **Reporting currency** defaults to the home country's own currency (Poland → PLN, Germany →
  EUR, Switzerland → CHF) unless set explicitly.
* **Reporting basis** says whether the entity reports `individual`, `consolidated` or `both`.

## Dates: inclusive at both ends

`valid_from` / `valid_until` (licences) and `effective_from` / `effective_until` (ownership) are
**inclusive**: a link ending `2025-12-31` still applies on 31 December and no longer on 1 January.
An empty end means open-ended. Queries take an `on` date (default today) so any past structure
can be reproduced.

## Ownership rules enforced on every new link

| Rule | Behaviour |
|---|---|
| Both entities exist in your tenant | otherwise 404 (same answer as "belongs to someone else") |
| No duplicate | the same parent cannot hold the same child twice in overlapping periods (409) |
| No cycles | A→B→C→A is refused; only links whose periods overlap the new one are considered (409) |
| At most 100 % | on any single day the holdings in one child may not exceed 100 % (409) |
| Percent format | plain `12` or `12.5`, at most 2 decimals; no exponent, no floating point |
| Branch | counts as 100 % and wholly part of its parent |

## Group view (indicative)

`GET /v1/entities/{lei}/group?on=DATE` lists everything the entity **controls** on that day,
directly or through controlled entities, with the direct percentage and the *effective*
percentage (product along the chain: 60 % × 80 % = 48 %). Holdings that do not reach control are
listed separately as `participations`.

**Control means "more than 50 % directly, or a branch".** Exactly 50 % is not control. Real
prudential or accounting consolidation (for example CRR Art. 18 or IFRS 10) can use other tests;
the response carries a note saying so. The control owner confirms the perimeter.

`GET /v1/entities/{lei}/ultimate-parent?on=DATE` climbs controlling links to the top.

Traversals stop at 25 levels or 5 000 members and return a clear error instead of exhausting
memory.

## Which rules apply to this entity

`GET /v1/entities/{lei}/rules?on=DATE` combines the registry with the rule engine:

1. Countries: the home country plus every country where a licence is valid **on that date**.
2. Basis: `individual` always if reported individually; `consolidated` only if the entity reports
   consolidated **and** actually has subsidiaries on that date (otherwise individual applies).
3. For each country and basis the approved, in-force rules for the entity type are collected and
   merged. Each result says `because_of` (countries and bases), and whether it is `in_transition`.

## Permissions

| Permission | viewer | analyst | compliance officer | tenant admin | ingestion service |
|---|---|---|---|---|---|
| `registry:read` | ✓ | ✓ | ✓ | ✓ | – |
| `registry:write` | – | ✓ | ✓ | ✓ | – |

Every write is audited in the same transaction as the change (`entity.created`,
`entity.updated`, `ownership.created`, `ownership.ended`).

## Example

```bash
# a bank and a subsidiary (LEIs must have valid check digits)
curl -X PUT  $API/v1/entities/$PARENT -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d '{"change_summary":"initial","entity":{"lei":"'$PARENT'","legal_name":"Example Bank AG",
       "entity_type":"bank","jurisdiction":"DE","reporting_basis":"both"}}'
curl -X POST $API/v1/ownership -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d '{"parent_lei":"'$PARENT'","child_lei":"'$CHILD'","percent":"80","effective_from":"2024-01-01"}'
curl "$API/v1/entities/$PARENT/group?on=2025-06-01" -H "Authorization: Bearer $T"
```

## Limits to be upfront about

* Control is a simplified, indicative test (above).
* Ownership data is entered by people (or later by feeds); the platform checks consistency, not truth.
* A hard cap of 100 000 links per tenant protects the service.
