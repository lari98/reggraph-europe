# Exposure graph and insurance connectivity (Phase 3)

Covers three modules: **exposure** (who is exposed to whom), **insurance** (who insures whom) and
**connectivity** (the two combined with the ownership data from the registry). Pitch Pack
reference: *Insurance Connectivity* and *Financial Exposure Graph*, MVP Stage 2.

Exit gate (from [PHASES.md](PHASES.md)): *graph queries reproducible; data lineage recorded.*
Both are tested — see "Reproducibility" and "Lineage" below.

## Data model

| Thing | Meaning | Mutable? |
|---|---|---|
| **Batch** | One load of exposure positions from a source system: source name, description, position count, SHA-256 of the content, loader, time | never |
| **Position** | `holder` is exposed to `counterparty` for `amount` `currency` as at `as_of` (optional `maturity_date`, `source_ref`) | never |
| **Contract version** | Insurer, insured, line (`life`, `non_life`, `health`, `credit_and_surety`, `reinsurance`), sum insured, period; a reinsurance contract names the contract it covers in `reinsures_ref` | new version per change, history kept |

Both ends of every position, and both parties of every contract, must already be registered
entities of the caller's tenant. The insurer must be registered as an *insurer* or *reinsurer*.
If a LEI is unknown the answer only says **how many** are unknown, never which, so a caller cannot
probe which LEIs exist in another tenant.

## Rules the code follows

| Id | Rule |
|---|---|
| X1 | Batches and positions are insert-only (database triggers refuse update and delete). |
| X2 | The same positions cannot be loaded twice in one tenant (content hash) — a retried upload is safe, and never double counts. |
| X3 | Holder and counterparty must be registered in the tenant. |
| X4 | "Current position" on day *D*: per `exposure_ref`, the one with the latest `as_of` ≤ *D* (a later load wins a tie, which is how a correction is made). A position whose `maturity_date` is before *D* has matured and drops out; on the maturity day itself it still counts. |
| X5 | `max_batch=N` restricts a query to batches 1..N. |
| X6 | A batch and its audit entry commit together. |
| Q1 | A query is fully described by tenant, date, currency, filters and `max_batch`. |
| Q2 | Results list the batches used (id and hash) and every exchange rate applied (rate and the rate's own date). |
| Q3 | A currency without a usable rate stops the query with 422. Nothing is guessed and nothing silently dropped. |
| Q4 | Each position is converted and rounded to the currency's minor units on its own, then summed, so totals do not depend on data order. |
| C3 | A contract counts on every day from `start_date` to `end_date` **inclusive**; no end date = open-ended. |
| C4 | Graph walks are limited to depth 4 and 500 nodes and expand neighbours in sorted order. |

Amounts are decimal **strings** (or integers). Floats, exponents (`1e6`), spaces, `NaN`,
negative values and more than 15 digits are refused.

## Reproducibility

Every analytic result carries `result_hash`: the SHA-256 of the canonical JSON of the result
(everything except the hash itself). Same inputs over the same loaded data give the same hash.

Loading newer data would normally change today's answer. To reproduce an earlier answer, repeat
the query with `max_batch` set to the highest batch number that existed then (that number is in
the earlier result's `lineage.batch_ids`). Tests: `test_queries_are_reproducible_with_max_batch`
and `test_ecosystem_reproducible_after_new_data`.

## Lineage

Each result tells you where every number came from:

```json
"lineage": {
  "as_of": "2026-03-02", "max_batch": null,
  "batch_ids": [1, 2], "batch_hashes": ["e11e…", "9a40…"],
  "positions_used": 4,
  "fx": [{"currency": "PLN", "rate": "4.0000000000", "rate_date": "2026-03-02"}]
}
```

Insurance results add `contract_lineage` (`contract_ref`, `version_no`, `content_hash`). From a
batch id you can read who loaded it, when, and from which source system
(`GET /v1/exposures/batches/{id}`), and the audit chain records every load.

## Queries

| Query | Question it answers | Notes |
|---|---|---|
| `edges` | Who is exposed to whom, in total, in one currency? | biggest first, ties by LEI |
| `concentration/{lei}` | How concentrated is one holder's exposure? | Shares to 4 decimals; **HHI** on the 0–10 000 scale; `top1`, `top3`. `by_group=true` merges counterparties that belong to one ownership group (their ultimate parent on that day). |
| `neighbourhood/{lei}` | What is within *n* steps of this entity, by exposure alone? | both directions, depth 0–4 |
| `entities/{lei}/insurance` | Who insures this entity, for how much, how concentrated is that cover, who reinsures it? | reinsurance contracts are shown as `reinsured_by`, not counted as cover for the insured |
| `entities/{lei}/interconnection` | Which entities are **both** a counterparty and an insurer of this one? | the double-hit cases: the loss on the exposure and loss of the cover |
| `entities/{lei}/ecosystem` | Everything within *n* steps, with typed edges `exposure`, `insurance`, `ownership` | one graph for the whole picture |

Worked example (test data): bank B lends 100 to corporate C, 40 (USD 50) to D and 50 (PLN 200)
to insurer I. Exposure shares are 52.63 % / 21.05 % / 26.32 %, HHI 3 905.82. Insurer I also
insures B for 1 000: `interconnection` reports I with exposure 50 and cover 1 000.

## What this does not do (yet)

* It is **not** a large-exposure or regulatory capital report. HHI and shares are analytical
  views; legal limits and netting rules come with the stress lab (Phase 4).
* Positions are taken as given. There is no netting, collateral or look-through to funds.
* Exposure to a holder's *own* group is shown like any other counterparty; filter it out in the
  caller if intra-group balances are not wanted.
* More than 50 000 current positions in one query are refused (422) rather than truncated;
  PostgreSQL storage in Phase 4 lifts this.
* Reinsurance chains are one step deep in the views (the contract named by `reinsures_ref`).
