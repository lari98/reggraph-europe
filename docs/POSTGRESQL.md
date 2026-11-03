# PostgreSQL storage (Phase 4)

SQLite stays the default (development, tests, small installs). PostgreSQL is selected with a URL.

```bash
pip install "reggraph-europe[postgres]"
export REGGRAPH_DATABASE_URL="postgresql://reggraph@db.example.internal/reggraph"
export REGGRAPH_DATABASE_SCHEMA="public"      # optional; lowercase letters, digits, underscore
```

With `REGGRAPH_DATABASE_URL` set, `REGGRAPH_DB_PATH` is ignored. In `prod` one of the two must
be set. The URL contains a password: it is never printed (excluded from `repr`) and never logged.
Put it in the environment or a secrets manager, not in a file in the repository. Use TLS to the
database (`?sslmode=verify-full`) outside a trusted network, and give the application role only the
rights it needs on its schema.

## How the two back ends stay identical

| Id | Rule |
|---|---|
| P1 | Migrations are written **once**, in a portable subset of the SQLite dialect, and translated for PostgreSQL (`storage/postgres.py::translate`). Tables, keys, indexes and checks are the same text; triggers become PL/pgSQL with the same messages. A test fails if a trigger cannot be translated. |
| P2 | All SQL uses `?` placeholders; the driver binds values (client-side, correctly escaped). SQL is never assembled from input. |
| P3 | Each transaction takes one advisory lock, like SQLite's `BEGIN IMMEDIATE`, so two application processes cannot hand out the same "next id". Tested with two connections writing one audit chain at once. |
| P4 | Immutability (rule versions, audit log, entities, exposure, contracts, scenarios, runs) is enforced by database triggers on both back ends. |
| P5 | Dates, amounts and hashes are stored as text on both, so values are byte-identical across back ends. |

**Proof:** the whole test suite (500 tests, including the attacker-style suite) was run twice,
once per back end, with the same expectations. To repeat it against your own server:

```bash
export REGGRAPH_TEST_PG_DSN="postgresql://user@localhost/postgres"
pytest          # each test gets its own throw-away schema and removes it afterwards
```

Without that variable the PostgreSQL-only tests are skipped (CI runs SQLite unless a database
service is added to the workflow).

## Operating notes

* Schema changes: add a numbered migration in `storage/sqlite.py`; both back ends pick it up.
  Keep to the portable subset (`CREATE TABLE/INDEX`, `CHECK`, `PRIMARY KEY`, the trigger shape used
  today). Anything else needs a dialect branch and a test on both.
* Moving existing SQLite data to PostgreSQL is **not** provided in this phase. Start PostgreSQL
  installations empty, or export/import through the API.
* Backups, point-in-time recovery and the restore drill belong to Phase 7.
* The database must use UTF-8 encoding; the client always asks for UTF-8.
