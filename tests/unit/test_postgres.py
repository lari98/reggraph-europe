"""PostgreSQL back end: translation (always) and live behaviour (when a server is configured)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

import pytest

from reggraph.config import Settings
from reggraph.core.clock import FixedClock
from reggraph.core.errors import ValidationFailure
from reggraph.security.audit import AuditLog
from reggraph.storage import Database, open_database
from reggraph.storage.postgres import PostgresDatabase, translate
from reggraph.storage.sqlite import MIGRATIONS
from tests.conftest import KEY, PG_DSN

live = pytest.mark.skipif(not PG_DSN, reason="set REGGRAPH_TEST_PG_DSN to run against PostgreSQL")


def test_trigger_translation() -> None:
    src = """
    CREATE TRIGGER t1 BEFORE UPDATE OF a, b
        ON tbl
        BEGIN SELECT RAISE(ABORT, 'nope'); END;
    CREATE TRIGGER t2 BEFORE UPDATE OF effective_until ON links
        WHEN OLD.effective_until IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'ended'); END;
    CREATE TRIGGER t3 BEFORE DELETE ON tbl
        BEGIN SELECT RAISE(ABORT, 'no delete'); END;
    INSERT INTO schema_version VALUES (9, datetime('now'));
    """
    out = translate(src)
    assert (
        "CREATE TRIGGER t1 BEFORE UPDATE OF a, b ON tbl FOR EACH ROW EXECUTE FUNCTION reggraph_abort('nope');"
        in out
    )
    assert (
        "FOR EACH ROW WHEN (OLD.effective_until IS NOT NULL) EXECUTE FUNCTION reggraph_abort('ended');"
        in out
    )
    assert "BEFORE DELETE ON tbl FOR EACH ROW EXECUTE FUNCTION reggraph_abort('no delete');" in out
    assert "now()::text" in out and "datetime(" not in out and "RAISE(" not in out


def test_every_migration_translates_completely() -> None:
    for version, script in MIGRATIONS:
        body = translate(script)
        assert "RAISE(" not in body and "datetime(" not in body, version
        assert body.count("CREATE TRIGGER") == script.count("CREATE TRIGGER"), version


def test_schema_names_are_validated() -> None:
    for bad in ("", "Public", "a-b", "a;drop", "1abc", "x" * 60, 'a"b'):
        with pytest.raises(ValidationFailure):
            PostgresDatabase("postgresql://invalid/none", bad)


def test_settings_never_print_the_database_url() -> None:
    s = Settings(
        env="test",
        token_keys={"k1": KEY},
        active_kid="k1",
        database_url="postgresql://u:hunter2@h/db",
    )
    assert "hunter2" not in repr(s) and "unit-test-signing" not in repr(s)
    prod = Settings(
        env="prod",
        token_keys={"k1": b"x" * 40},
        active_kid="k1",
        database_url="postgresql://u:p@h/db",
    )
    assert prod.database_url
    with pytest.raises(ValidationFailure):
        Settings(
            env="prod", token_keys={"k1": b"x" * 40}, active_kid="k1"
        )  # still needs a persistent store
    env = {
        "REGGRAPH_TOKEN_KEYS": "k1:" + "a" * 40,
        "REGGRAPH_DATABASE_URL": "postgresql://h/d",
        "REGGRAPH_DATABASE_SCHEMA": "app",
    }
    loaded = Settings.from_env(env)
    assert loaded.database_url == "postgresql://h/d" and loaded.database_schema == "app"


def test_default_is_sqlite() -> None:
    db = open_database()
    assert isinstance(db, Database)
    db.close()


@pytest.fixture
def pg() -> PostgresDatabase:  # type: ignore[misc]
    db = PostgresDatabase(PG_DSN, f"t_{uuid.uuid4().hex[:16]}")
    yield db
    db.drop_schema()
    db.close()


@live
def test_migrations_are_applied_once(pg: PostgresDatabase) -> None:
    again = PostgresDatabase(PG_DSN, pg._schema)
    versions = [
        r["version"] for r in again.query("SELECT version FROM schema_version ORDER BY version")
    ]
    again.close()
    assert versions == [v for v, _ in MIGRATIONS]


@live
def test_schemas_are_isolated(pg: PostgresDatabase) -> None:
    other = PostgresDatabase(PG_DSN, f"t_{uuid.uuid4().hex[:16]}")
    try:
        pg.execute("INSERT INTO fx_rates VALUES ('a', 'USD', '1.1', '2026-01-01', 's')")
        assert other.query("SELECT COUNT(*) AS n FROM fx_rates")[0]["n"] == 0
    finally:
        other.drop_schema()
        other.close()


@live
def test_rollback_and_nesting(pg: PostgresDatabase) -> None:
    insert = "INSERT INTO fx_rates VALUES ('a', ?, '1.1', '2026-01-01', 's')"

    def failing() -> None:
        with pg.transaction():
            pg.execute(insert, ("USD",))
            with pg.transaction():  # joins the outer one
                pg.execute(insert, ("CHF",))
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing()
    assert pg.query("SELECT COUNT(*) AS n FROM fx_rates")[0]["n"] == 0
    with pg.transaction(), pg.transaction():
        pg.execute(insert, ("USD",))
    assert pg.query("SELECT COUNT(*) AS n FROM fx_rates")[0]["n"] == 1


@live
def test_parameters_are_data_not_sql(pg: PostgresDatabase) -> None:
    evil = "x'); DROP TABLE fx_rates; --"
    pg.execute(
        "INSERT INTO fx_rates VALUES ('a', ?, '100%', '2026-01-01', ?)",
        (evil[:3], "100% ? %s %(x)s"),
    )
    row = pg.query("SELECT per_eur, source FROM fx_rates WHERE quote = ?", ("x')",))[0]
    assert row["per_eur"] == "100%" and row["source"] == "100% ? %s %(x)s"
    assert pg.query("SELECT COUNT(*) AS n FROM fx_rates WHERE quote = ?", (evil,))[0]["n"] == 0
    assert (
        pg.query("SELECT COUNT(*) AS n FROM fx_rates WHERE (? IS NULL OR quote = ?)", (None, None))[
            0
        ]["n"]
        == 1
    )


@live
def test_concurrent_writers_do_not_collide(pg: PostgresDatabase) -> None:
    """Two connections appending to one audit chain: ids stay unique, the chain stays valid."""
    clock = FixedClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))
    second = PostgresDatabase(PG_DSN, pg._schema)
    errors: list[BaseException] = []

    def work(db: PostgresDatabase, tag: str) -> None:
        log = AuditLog(db, clock)
        try:
            for i in range(20):
                log.append("acme", f"user:{tag}", "test.event", f"{tag}{i}", {"i": i})
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=work, args=(pg, "a")),
        threading.Thread(target=work, args=(second, "b")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    second.close()
    assert errors == []
    log = AuditLog(pg, clock)
    assert log.verify("acme").ok and log.head("acme")[0] == 40


@live
def test_immutability_triggers_fire(pg: PostgresDatabase) -> None:
    pg.execute("INSERT INTO fx_rates VALUES ('a', 'USD', '1.1', '2026-01-01', 's')")
    with pytest.raises(Exception, match="insert-only"):
        pg.execute("UPDATE fx_rates SET per_eur = '9'")
    pg.execute("INSERT INTO audit_log VALUES ('a', 1, 't', 'u', 'x', 's', '{}', 'p', 'h')")
    with pytest.raises(Exception, match="append-only"):
        pg.execute("DELETE FROM audit_log")
