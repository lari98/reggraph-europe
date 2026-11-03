"""SQLite access layer.

Rules
-----
====  ==========================================================================
S1    Every statement is parameterised (``?``). SQL is never built from input.
S2    Every table has ``tenant_id`` and every query filters on it (tenant isolation).
S3    Rule versions and audit entries are immutable: database triggers block deleting them
      and changing their content (defence in depth; the audit hash chain still detects an
      attacker who removes the triggers).
S4    Schema changes are numbered migrations applied once, in order.
S5    One lock guards the connection, so API worker threads cannot interleave a transaction.
====  ==========================================================================
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any, Final

MIGRATIONS: Final[tuple[tuple[int, str], ...]] = (
    (
        1,
        """
        CREATE TABLE rule_versions (
            tenant_id TEXT NOT NULL,
            rule_key TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('draft','approved','rejected','superseded','withdrawn')),
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            change_summary TEXT NOT NULL,
            source TEXT NOT NULL,
            proposed_by TEXT NOT NULL,
            proposed_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT,
            decision_note TEXT,
            PRIMARY KEY (tenant_id, rule_key, version_no)
        );
        CREATE INDEX idx_rule_status ON rule_versions (tenant_id, status);
        CREATE TABLE review_tasks (
            tenant_id TEXT NOT NULL,
            task_id INTEGER NOT NULL,
            rule_key TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            owner TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open','done')),
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, task_id)
        );
        CREATE TABLE audit_log (
            tenant_id TEXT NOT NULL,
            n INTEGER NOT NULL,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            subject TEXT NOT NULL,
            detail_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            entry_hash TEXT NOT NULL,
            PRIMARY KEY (tenant_id, n)
        );
        CREATE TABLE fx_rates (
            tenant_id TEXT NOT NULL,
            quote TEXT NOT NULL,
            per_eur TEXT NOT NULL,
            as_of TEXT NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (tenant_id, quote, as_of)
        );
        CREATE TRIGGER rule_content_immutable
            BEFORE UPDATE OF tenant_id, rule_key, version_no, payload_json, content_hash,
                             change_summary, source, proposed_by, proposed_at
            ON rule_versions
            BEGIN SELECT RAISE(ABORT, 'rule version content is immutable'); END;
        CREATE TRIGGER rule_no_delete BEFORE DELETE ON rule_versions
            BEGIN SELECT RAISE(ABORT, 'rule versions cannot be deleted'); END;
        CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
        CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
        CREATE TRIGGER fx_no_update BEFORE UPDATE ON fx_rates
            BEGIN SELECT RAISE(ABORT, 'fx rates are insert-only'); END;
        INSERT INTO schema_version VALUES (1, datetime('now'));
        """,
    ),
    (
        2,
        """
        CREATE TABLE entity_versions (
            tenant_id TEXT NOT NULL,
            lei TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            legal_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active','dissolved')),
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            change_summary TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, lei, version_no)
        );
        CREATE INDEX idx_entity_filter ON entity_versions (tenant_id, jurisdiction, entity_type);
        CREATE TABLE ownership_links (
            tenant_id TEXT NOT NULL,
            link_id INTEGER NOT NULL,
            parent_lei TEXT NOT NULL,
            child_lei TEXT NOT NULL,
            percent TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('subsidiary','participation','branch')),
            effective_from TEXT NOT NULL,
            effective_until TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, link_id),
            CHECK (parent_lei <> child_lei)
        );
        CREATE INDEX idx_own_parent ON ownership_links (tenant_id, parent_lei);
        CREATE INDEX idx_own_child ON ownership_links (tenant_id, child_lei);
        CREATE TRIGGER entity_immutable BEFORE UPDATE ON entity_versions
            BEGIN SELECT RAISE(ABORT, 'entity versions are immutable'); END;
        CREATE TRIGGER entity_no_delete BEFORE DELETE ON entity_versions
            BEGIN SELECT RAISE(ABORT, 'entity versions cannot be deleted'); END;
        CREATE TRIGGER ownership_immutable
            BEFORE UPDATE OF tenant_id, link_id, parent_lei, child_lei, percent, kind,
                             effective_from, created_by, created_at
            ON ownership_links
            BEGIN SELECT RAISE(ABORT, 'ownership link content is immutable'); END;
        CREATE TRIGGER ownership_end_once BEFORE UPDATE OF effective_until ON ownership_links
            WHEN OLD.effective_until IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'ownership link already ended'); END;
        CREATE TRIGGER ownership_no_delete BEFORE DELETE ON ownership_links
            BEGIN SELECT RAISE(ABORT, 'ownership links cannot be deleted'); END;
        INSERT INTO schema_version VALUES (2, datetime('now'));
        """,
    ),
    (
        3,
        """
        CREATE TABLE exposure_batches (
            tenant_id TEXT NOT NULL,
            batch_id INTEGER NOT NULL,
            source_system TEXT NOT NULL,
            description TEXT NOT NULL,
            position_count INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            loaded_by TEXT NOT NULL,
            loaded_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, batch_id),
            UNIQUE (tenant_id, content_hash)
        );
        CREATE TABLE exposure_positions (
            tenant_id TEXT NOT NULL,
            position_id INTEGER NOT NULL,
            batch_id INTEGER NOT NULL,
            exposure_ref TEXT NOT NULL,
            holder_lei TEXT NOT NULL,
            counterparty_lei TEXT NOT NULL,
            exposure_type TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount TEXT NOT NULL,
            as_of TEXT NOT NULL,
            maturity_date TEXT,
            source_ref TEXT,
            recorded_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, position_id),
            CHECK (holder_lei <> counterparty_lei)
        );
        CREATE INDEX idx_pos_ref ON exposure_positions (tenant_id, exposure_ref, as_of);
        CREATE INDEX idx_pos_holder ON exposure_positions (tenant_id, holder_lei);
        CREATE INDEX idx_pos_cpty ON exposure_positions (tenant_id, counterparty_lei);
        CREATE TABLE insurance_contract_versions (
            tenant_id TEXT NOT NULL,
            contract_ref TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            insurer_lei TEXT NOT NULL,
            insured_lei TEXT NOT NULL,
            line TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            change_summary TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, contract_ref, version_no)
        );
        CREATE INDEX idx_ins_insurer ON insurance_contract_versions (tenant_id, insurer_lei);
        CREATE INDEX idx_ins_insured ON insurance_contract_versions (tenant_id, insured_lei);
        CREATE TRIGGER batch_immutable BEFORE UPDATE ON exposure_batches
            BEGIN SELECT RAISE(ABORT, 'exposure batches are immutable'); END;
        CREATE TRIGGER batch_no_delete BEFORE DELETE ON exposure_batches
            BEGIN SELECT RAISE(ABORT, 'exposure batches cannot be deleted'); END;
        CREATE TRIGGER position_immutable BEFORE UPDATE ON exposure_positions
            BEGIN SELECT RAISE(ABORT, 'exposure positions are immutable'); END;
        CREATE TRIGGER position_no_delete BEFORE DELETE ON exposure_positions
            BEGIN SELECT RAISE(ABORT, 'exposure positions cannot be deleted'); END;
        CREATE TRIGGER contract_immutable BEFORE UPDATE ON insurance_contract_versions
            BEGIN SELECT RAISE(ABORT, 'contract versions are immutable'); END;
        CREATE TRIGGER contract_no_delete BEFORE DELETE ON insurance_contract_versions
            BEGIN SELECT RAISE(ABORT, 'contract versions cannot be deleted'); END;
        INSERT INTO schema_version VALUES (3, datetime('now'));
        """,
    ),
    (
        4,
        """
        CREATE TABLE stress_scenario_versions (
            tenant_id TEXT NOT NULL,
            scenario_key TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            change_summary TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, scenario_key, version_no)
        );
        CREATE TABLE stress_runs (
            tenant_id TEXT NOT NULL,
            run_id INTEGER NOT NULL,
            scenario_key TEXT NOT NULL,
            scenario_version INTEGER NOT NULL,
            as_of TEXT NOT NULL,
            currency TEXT NOT NULL,
            max_batch INTEGER NOT NULL,
            input_hash TEXT NOT NULL,
            result_hash TEXT NOT NULL,
            signature TEXT NOT NULL,
            scenario_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, run_id)
        );
        CREATE INDEX idx_runs_scenario ON stress_runs (tenant_id, scenario_key);
        CREATE TRIGGER scenario_immutable BEFORE UPDATE ON stress_scenario_versions
            BEGIN SELECT RAISE(ABORT, 'scenario versions are immutable'); END;
        CREATE TRIGGER scenario_no_delete BEFORE DELETE ON stress_scenario_versions
            BEGIN SELECT RAISE(ABORT, 'scenario versions cannot be deleted'); END;
        CREATE TRIGGER run_immutable BEFORE UPDATE ON stress_runs
            BEGIN SELECT RAISE(ABORT, 'stress runs are immutable'); END;
        CREATE TRIGGER run_no_delete BEFORE DELETE ON stress_runs
            BEGIN SELECT RAISE(ABORT, 'stress runs cannot be deleted'); END;
        INSERT INTO schema_version VALUES (4, datetime('now'));
        """,
    ),
)

Params = Sequence[Any]


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        # isolation_level=None: we manage transactions explicitly (autocommit otherwise).
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._lock = threading.RLock()
        self._depth = 0
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = int(row["v"] or 0)
            for version, script in MIGRATIONS:
                if version > current:
                    # Each script ends with its own ``INSERT INTO schema_version`` so the schema
                    # change and its version record commit together (or not at all).
                    self._conn.executescript("BEGIN;" + script + "COMMIT;")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """All-or-nothing block. Nested use joins the outer transaction."""
        with self._lock:
            if self._depth > 0:
                self._depth += 1
                try:
                    yield self._conn
                finally:
                    self._depth -= 1
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._depth = 0

    def query(self, sql: str, params: Params = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def execute(self, sql: str, params: Params = ()) -> int:
        """Run one statement. Returns the number of affected rows."""
        with self._lock:
            return self._conn.execute(sql, tuple(params)).rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
