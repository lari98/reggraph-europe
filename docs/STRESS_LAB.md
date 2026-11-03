# Stress testing laboratory (Phase 4)

Pitch Pack: *Stress Testing Laboratory*, MVP Stage 3. Exit gate (from [PHASES.md](PHASES.md)):
**same inputs → same outputs; results signed and versioned.** Both are tested.

## What it does

1. You define a **scenario**: a named, versioned list of shocks.
2. You **run** it for a date and a currency. The run freezes its inputs, computes the result,
   signs it and stores it. Nothing about a stored run can change.
3. You (or an auditor) can **verify** any run later: the result is recomputed from the stored
   inputs and compared, and the signature is checked.

## Shocks

| Kind | Fields | Effect |
|---|---|---|
| `fx` | `currency`, `change_percent` (−100…100) | A *fall* of that currency against the euro writes down positions held in it. A rise is ignored (no gain is booked). |
| `default` | `leis`, `loss_given_default_percent` (0…100], `include_group` (default true) | The named entities fail; counterparties lose that share of what they owe. With `include_group`, the controlled group members (registry, on the run date) fail too. |
| `haircut` | `percent`, `exposure_type` and/or `counterparty_jurisdiction` | Flat write-down of matching positions. Both filters given = both must match. |
| `insurer_failure` | `leis` | These insurers pay nothing. |

`use_insurance` (default true) switches the recovery step on or off.

Percentages are plain decimal text with at most two decimals: `"12"`, `"-12.5"`. Exponents,
floats, `NaN`, spaces and out-of-range values are refused. A scenario has 1–50 shocks.

## The calculation (engine rules)

| Id | Rule |
|---|---|
| E1 | The engine is a pure function: no database, clock or randomness. |
| E2 | For each position the surviving fraction is the **product** of what every shock leaves, so shock order never matters. |
| E3 | Loss = amount × (1 − surviving fraction), rounded half-even to the currency's minor units per aggregated row. Totals are exact sums. |
| E4 | Recovery for a holder = min(gross loss, cover). Cover = sum insured of its active contracts, each scaled by its insurer's surviving fraction (0 for `insurer_failure`). Reinsurance contracts are not counted as cover for the insured. |

Per holder the result gives exposure, gross loss, insurance recovery, net loss, net loss ratio
(4 decimals), optional capital buffer and a `breach` flag (net loss above buffer). It also lists
the 20 counterparties that caused the most loss and the totals.

## Frozen inputs, signing, verification

| Id | Rule |
|---|---|
| R1 | A run stores a **snapshot**: positions (aggregated by holder, counterparty, type, currency, converted to the run currency), covers, group members, exchange rates, batch and contract lineage, capital buffers, and the scenario version exactly as run. |
| R2 | `max_batch` is made concrete (newest batch when you give none). |
| R3 | The signature is HMAC-SHA256 over tenant, run id, scenario@version, input hash and result hash, with the active key id (`k1.<hex>`). It is domain-separated from access tokens. |
| R4 | `verify` recomputes the result from the stored snapshot and reports `input_hash_ok`, `result_reproduced`, `result_hash_ok`, `signature_ok`, `scenario_matches_version`. `ok` needs all five. |
| R5 | Later data, new rates or changed contracts never affect an old run. To compare, run again. |

Same scenario version, same date, currency, buffers and data → identical `input_hash`,
`result_hash` and result. Only `run_id`, time and signature differ.
A signature proves origin and integrity within this deployment; it is **not** a legal e-signature.
Key rotation: keep the old key id in `REGGRAPH_TOKEN_KEYS`; removing it makes its runs fail verification.

## Limits and simplifications (read before relying on numbers)

* Insurance is assumed to respond to the loss up to the sum insured. Real policy terms
  (deductibles, exclusions, trigger conditions) are not modelled.
* No second-round effects: a failure does not change other entities' solvency except through the
  shocks you list and through group membership.
* No netting or collateral; exposure types are labels for filtering.
* Capital buffers are what *you* supply per run. They are inputs, not computed regulatory capital.
* At most 20 000 distinct exposure rows per run (refused above that, never truncated) and 10 000 runs per tenant.
* Results are decision support, not a regulatory stress-test submission.

## API

See [API.md](API.md): `PUT/GET /v1/stress/scenarios…`, `POST/GET /v1/stress/runs…`,
`GET …/snapshot`, `POST …/verify`. Running needs `stress:write` plus read access to exposure,
insurance and registry data.
