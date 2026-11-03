# Coding standards (tabular protocol)

Each rule has an ID so reviews can cite it ("violates S3"). Tools enforce what they can.

## Structure and style

| ID | Rule | Enforced by |
|---|---|---|
| C1 | Python ≥ 3.11, `from __future__ import annotations` in every module | ruff `UP`, review |
| C2 | Line length 100, formatter-owned layout | `ruff format` |
| C3 | Every public function, class and module has a docstring saying **why**, not what | review |
| C4 | Full type hints; `mypy --strict` must pass | mypy |
| C5 | One responsibility per module; dependencies point down ([ARCHITECTURE.md](ARCHITECTURE.md)) | review |
| C6 | No `print`; no commented-out code; no TODO without an issue id | ruff `T20` |
| C7 | Names say what a thing is: `rule_key`, not `rk` | review |

## Correctness

| ID | Rule | Enforced by |
|---|---|---|
| K1 | Money is `Decimal` inside `Money`; never `float`; amounts cross the API as strings | `Money`, tests |
| K2 | Time comes from an injected `Clock`, never `datetime.now()` in domain code | ruff `DTZ`, review |
| K3 | Domain failures raise a `RegGraphError` subclass with a translation key | review |
| K4 | Data classes are frozen unless mutation is the point | review |
| K5 | A function that can fail on bad input fails *early* with a clear, translated message | tests |

## Security

| ID | Rule | Enforced by |
|---|---|---|
| S1 | Every SQL statement uses `?` placeholders; no string-built SQL | ruff `S608`, bandit, tests |
| S2 | All outside text passes `clean_text`; identifiers pass `ensure_identifier` | tests |
| S3 | Tenant id comes only from the verified principal | `tests/security` isolation tests |
| S4 | Every route declares its permission with `require(...)` | review, 401/403 tests |
| S5 | Request models use `extra="forbid"` (no mass assignment) | tests |
| S6 | Errors shown to callers are generic and translated; details go to the log only | tests |
| S7 | No secrets in code, logs or error messages; keys come from the environment | config checks, review |
| S8 | No `eval`, `exec`, `pickle`, `shell=True`, `yaml.load`, `format()` on user text | bandit, ruff `S` |

## Tests

| ID | Rule | Enforced by |
|---|---|---|
| T1 | New behaviour → unit test; new route → integration test; new input → attacker test | review |
| T2 | Tests are deterministic: fixed clock, in-memory database, no network | fixtures |
| T3 | Bug fix starts with a failing test that reproduces it | review |
| T4 | Coverage gate 90 % (`--cov-fail-under`) | pytest |
| T5 | Attack tests carry the `security` marker and live in `tests/security/` | pytest markers |

## Comments

Comments explain **intent and constraints** (why this limit exists, which threat it stops).
They do not repeat the code. Tables in docstrings (see `rbac.py`, `middleware.py`, `routes.py`)
are the preferred form for permission matrices and route lists.

## Commit and review

| ID | Rule |
|---|---|
| R1 | One logical change per commit; message says what and why |
| R2 | `make check` is green before review |
| R3 | The touched module is bumped (`tools/module_manifest.py bump`) with a one-line reason |
| R4 | Two-person review for anything in `security/`, `regulation/store.py` or `storage/` |
