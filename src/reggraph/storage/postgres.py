"""PostgreSQL back end (Phase 4), same interface as the SQLite one.

====  ==========================================================================================
P1    Same schema, same SQL: migrations are written once (SQLite dialect, a portable subset) and
      translated here, so the two back ends cannot drift apart.
P2    Parameters are bound by the driver (client-side binding with correct escaping, which also
      lets ``(? IS NULL OR col = ?)`` work with untyped NULLs). SQL is never built from input.
P3    One advisory lock per transaction mirrors SQLite's ``BEGIN IMMEDIATE``: writers are
      serialised across processes, so "next id" counters cannot collide.
P4    Immutability is enforced by PL/pgSQL triggers with the same messages as on SQLite.
P5    The schema name is validated (letters, digits, underscore) and quoted by the driver.
====  ==========================================================================================

Needs the optional dependency ``psycopg`` (``pip install reggraph-europe[postgres]``).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

from reggraph.core.errors import ValidationFailure
from reggraph.storage.base import Params, Row
from reggraph.storage.sqlite import MIGRATIONS

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only without the optional extra
    psycopg = None  # type: ignore[assignment]

_SCHEMA_RE: Final = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
_LOCK_KEY: Final = 727_274  # arbitrary constant: "the reggraph writer lock"

_TRIGGER_FN: Final = """
CREATE FUNCTION reggraph_abort() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'integrity_constraint_violation';
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_RE: Final = re.compile(
    r"CREATE TRIGGER (?P<name>\w+)\s+BEFORE (?P<event>UPDATE(?: OF [\w,\s]+?)?|DELETE)\s+"
    r"ON (?P<table>\w+)\s+(?:WHEN (?P<when>.+?)\s+)?"
    r"BEGIN SELECT RAISE\(ABORT, '(?P<msg>[^']*)'\); END;",
    re.DOTALL,
)


def translate(script: str) -> str:
    """Turn a (portable-subset) SQLite migration into PostgreSQL."""

    def trigger(m: re.Match[str]) -> str:
        when = f" WHEN ({' '.join(m['when'].split())})" if m["when"] else ""
        return (
            f"CREATE TRIGGER {m['name']} BEFORE {' '.join(m['event'].split())} ON {m['table']}"
            f" FOR EACH ROW{when} EXECUTE FUNCTION reggraph_abort('{m['msg']}');"
        )

    out = _TRIGGER_RE.sub(trigger, script)
    out = out.replace("datetime('now')", "now()::text")
    if "RAISE(" in out:  # pragma: no cover - guards against a trigger shape we cannot translate
        raise ValueError("untranslated trigger in migration")
    return out


class PostgresDatabase:
    def __init__(self, dsn: str, schema: str = "public") -> None:
        if psycopg is None:  # pragma: no cover
            raise ValidationFailure(
                "PostgreSQL support needs: pip install reggraph-europe[postgres]"
            )
        if not _SCHEMA_RE.match(schema):
            raise ValidationFailure("invalid database schema name")
        self._schema = schema
        self._conn: Any = psycopg.connect(
            dsn,
            autocommit=True,
            row_factory=dict_row,
            cursor_factory=psycopg.ClientCursor,
            client_encoding="UTF8",  # text always arrives as str, whatever the server default
        )
        self._lock = threading.RLock()
        self._depth = 0
        self._conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
        self._conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
            try:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
                current = int(row["v"] or 0)
                for version, script in MIGRATIONS:
                    if version <= current:
                        continue
                    body = translate(script)
                    if version == 1:
                        body = _TRIGGER_FN + body
                    self._conn.execute("BEGIN")
                    try:
                        self._conn.execute(body)
                    except BaseException:
                        self._conn.execute("ROLLBACK")
                        raise
                    self._conn.execute("COMMIT")
            finally:
                self._conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))

    @staticmethod
    def _bind(statement: str) -> str:
        return statement.replace("%", "%%").replace("?", "%s")

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        """All-or-nothing block. Nested use joins the outer transaction."""
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield self._conn
                finally:
                    self._depth -= 1
                return
            self._conn.execute("BEGIN")
            self._depth = 1
            try:
                self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._depth = 0

    def query(self, sql_text: str, params: Params = ()) -> list[Row]:
        with self._lock:
            cur = self._conn.execute(self._bind(sql_text), tuple(params))
            return list(cur.fetchall())

    def execute(self, sql_text: str, params: Params = ()) -> int:
        with self._lock:
            return int(self._conn.execute(self._bind(sql_text), tuple(params)).rowcount)

    def drop_schema(self) -> None:
        """Test helper: remove everything this instance created."""
        with self._lock:
            self._conn.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(self._schema))
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
