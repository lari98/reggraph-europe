"""Scenario versions and stored runs (both insert-only, both audited).

====  ======================================================================================
S1    Scenario versions are append-only; an update writes version n+1; identical = 409.
S2    A run stores everything needed to reproduce and check it: the scenario version as run,
      the full input snapshot, the result, their hashes and a signature.
S3    Runs are immutable (database triggers). Nothing about a past run can be edited.
S4    Scenario and run commit together with their audit entry.
====  ======================================================================================
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict

from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, NotFoundError
from reggraph.registry.store import EntityStore
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.security.validation import clean_text, ensure_identifier
from reggraph.storage.base import Db, Row
from reggraph.stress.models import ScenarioInput, ScenarioVersion

_MAX_PAGE: Final = 200
MAX_RUNS_PER_TENANT: Final = 10_000


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: int
    scenario_key: str
    scenario_version: int
    as_of: str
    currency: str
    max_batch: int
    input_hash: str
    result_hash: str
    signature: str
    created_by: str
    created_at: datetime


class StoredRun(BaseModel):
    """A run with its stored documents (JSON text exactly as hashed)."""

    model_config = ConfigDict(frozen=True)

    record: RunRecord
    scenario_json: str
    inputs_json: str
    result_json: str


class StressStore:
    def __init__(self, db: Db, audit: AuditLog, clock: Clock, registry: EntityStore) -> None:
        self._db = db
        self._audit = audit
        self._clock = clock
        self._registry = registry

    # ------------------------------------------------------------------ scenarios
    @staticmethod
    def _to_version(row: Row) -> ScenarioVersion:
        return ScenarioVersion(
            scenario_key=row["scenario_key"],
            version_no=row["version_no"],
            scenario=ScenarioInput.model_validate_json(row["payload_json"]),
            content_hash=row["content_hash"],
            change_summary=row["change_summary"],
            changed_by=row["changed_by"],
            changed_at=datetime.fromisoformat(row["changed_at"]),
        )

    def _history(self, tenant: str, key: str) -> list[ScenarioVersion]:
        rows = self._db.query(
            "SELECT * FROM stress_scenario_versions WHERE tenant_id = ? AND scenario_key = ?"
            " ORDER BY version_no",
            (tenant, key),
        )
        return [self._to_version(r) for r in rows]

    def upsert_scenario(
        self, who: Principal, scenario: ScenarioInput, change_summary: str
    ) -> ScenarioVersion:
        who.require(Permission.STRESS_WRITE)
        summary = clean_text(change_summary, max_len=500)
        tenant, now = who.tenant_id, self._clock.now()
        actor = f"user:{who.user_id}"
        with self._db.transaction():
            existing = self._history(tenant, scenario.scenario_key)
            if existing and existing[-1].content_hash == scenario.content_hash():
                raise ConflictError("scenario is identical to its current version")
            version_no = existing[-1].version_no + 1 if existing else 1
            self._db.execute(
                "INSERT INTO stress_scenario_versions (tenant_id, scenario_key, version_no,"
                " payload_json, content_hash, change_summary, changed_by, changed_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    scenario.scenario_key,
                    version_no,
                    scenario.model_dump_json(),
                    scenario.content_hash(),
                    summary,
                    actor,
                    now.isoformat(),
                ),
            )
            self._audit.append(
                tenant,
                actor,
                "scenario.created" if version_no == 1 else "scenario.updated",
                f"{scenario.scenario_key}@{version_no}",
                {"content_hash": scenario.content_hash()},
            )
            return self._history(tenant, scenario.scenario_key)[-1]

    def get_scenario(self, who: Principal, key: str, version: int | None = None) -> ScenarioVersion:
        who.require(Permission.STRESS_READ)
        versions = self._history(who.tenant_id, ensure_identifier(key, "scenario key"))
        if not versions:
            raise NotFoundError("scenario not found")
        if version is None:
            return versions[-1]
        for v in versions:
            if v.version_no == version:
                return v
        raise NotFoundError("scenario version not found")

    def scenario_history(self, who: Principal, key: str) -> list[ScenarioVersion]:
        who.require(Permission.STRESS_READ)
        versions = self._history(who.tenant_id, ensure_identifier(key, "scenario key"))
        if not versions:
            raise NotFoundError("scenario not found")
        return versions

    def list_scenarios(
        self, who: Principal, *, limit: int = 50, after: str = ""
    ) -> list[ScenarioVersion]:
        who.require(Permission.STRESS_READ)
        limit = max(1, min(limit, _MAX_PAGE))
        tenant = who.tenant_id
        rows = self._db.query(
            "SELECT s.* FROM stress_scenario_versions s JOIN ("
            " SELECT scenario_key, MAX(version_no) AS v FROM stress_scenario_versions"
            " WHERE tenant_id = ? GROUP BY scenario_key) m"
            " ON s.scenario_key = m.scenario_key AND s.version_no = m.v"
            " WHERE s.tenant_id = ? AND s.scenario_key > ? ORDER BY s.scenario_key LIMIT ?",
            (tenant, tenant, after, limit),
        )
        return [self._to_version(r) for r in rows]

    # ------------------------------------------------------------------ runs
    @staticmethod
    def _to_record(row: Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            scenario_key=row["scenario_key"],
            scenario_version=row["scenario_version"],
            as_of=row["as_of"],
            currency=row["currency"],
            max_batch=row["max_batch"],
            input_hash=row["input_hash"],
            result_hash=row["result_hash"],
            signature=row["signature"],
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def next_run_id(self, tenant: str) -> int:
        rows = self._db.query(
            "SELECT COALESCE(MAX(run_id), 0) + 1 AS n FROM stress_runs WHERE tenant_id = ?",
            (tenant,),
        )
        return int(rows[0]["n"])

    def count_runs(self, tenant: str) -> int:
        rows = self._db.query(
            "SELECT COUNT(*) AS n FROM stress_runs WHERE tenant_id = ?", (tenant,)
        )
        return int(rows[0]["n"])

    def save_run(
        self,
        who: Principal,
        record: RunRecord,
        *,
        scenario_json: str,
        inputs_json: str,
        result_json: str,
    ) -> RunRecord:
        """Insert inside the caller's transaction (run id and signature already fixed)."""
        tenant = who.tenant_id
        actor = f"user:{who.user_id}"
        self._db.execute(
            "INSERT INTO stress_runs (tenant_id, run_id, scenario_key, scenario_version, as_of,"
            " currency, max_batch, input_hash, result_hash, signature, scenario_json,"
            " snapshot_json, result_json, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                tenant,
                record.run_id,
                record.scenario_key,
                record.scenario_version,
                record.as_of,
                record.currency,
                record.max_batch,
                record.input_hash,
                record.result_hash,
                record.signature,
                scenario_json,
                inputs_json,
                result_json,
                actor,
                record.created_at.isoformat(),
            ),
        )
        self._audit.append(
            tenant,
            actor,
            "stress.run",
            f"run#{record.run_id}",
            {
                "scenario": f"{record.scenario_key}@{record.scenario_version}",
                "input_hash": record.input_hash,
                "result_hash": record.result_hash,
            },
        )
        return record

    def get_run(self, who: Principal, run_id: int) -> StoredRun:
        who.require(Permission.STRESS_READ)
        rows = self._db.query(
            "SELECT * FROM stress_runs WHERE tenant_id = ? AND run_id = ?",
            (who.tenant_id, run_id),
        )
        if not rows:
            raise NotFoundError("stress run not found")
        r = rows[0]
        return StoredRun(
            record=self._to_record(r),
            scenario_json=r["scenario_json"],
            inputs_json=r["snapshot_json"],
            result_json=r["result_json"],
        )

    def list_runs(
        self, who: Principal, *, scenario_key: str | None = None, limit: int = 50, after: int = 0
    ) -> list[RunRecord]:
        who.require(Permission.STRESS_READ)
        limit = max(1, min(limit, _MAX_PAGE))
        key = ensure_identifier(scenario_key, "scenario key") if scenario_key else None
        rows = self._db.query(
            "SELECT * FROM stress_runs WHERE tenant_id = ? AND run_id > ?"
            " AND (? IS NULL OR scenario_key = ?) ORDER BY run_id LIMIT ?",
            (who.tenant_id, after, key, key, limit),
        )
        return [self._to_record(r) for r in rows]
