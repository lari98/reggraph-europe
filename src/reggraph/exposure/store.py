"""Exposure storage: immutable batches and positions with lineage.

====  ======================================================================================
X1    Batches and positions are insert-only (database triggers enforce it).
X2    The same content cannot be loaded twice in a tenant (content hash), so a retry is safe.
X3    Holder and counterparty must both be registered entities of the caller's tenant.
X4    "Current" means: per exposure_ref, the latest ``as_of`` not after the query date (ties go
      to the later load); matured positions (maturity before the query date) drop out.
X5    ``max_batch`` limits a query to batches up to that number, so a past answer can be
      reproduced exactly after newer batches have been loaded.
X6    A batch and its audit entry commit in one transaction.
====  ======================================================================================
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, NotFoundError, ValidationFailure
from reggraph.exposure.models import Batch, BatchInput, Position, PositionInput
from reggraph.registry.store import EntityStore
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.storage.sqlite import Database

MAX_POSITIONS_PER_TENANT: Final = 1_000_000
MAX_QUERY_POSITIONS: Final = 50_000
_MAX_PAGE: Final = 200


class ExposureStore:
    def __init__(self, db: Database, audit: AuditLog, clock: Clock, registry: EntityStore) -> None:
        self._db = db
        self._audit = audit
        self._clock = clock
        self._registry = registry

    @staticmethod
    def _to_batch(row: sqlite3.Row) -> Batch:
        return Batch(
            batch_id=row["batch_id"],
            source_system=row["source_system"],
            description=row["description"],
            position_count=row["position_count"],
            content_hash=row["content_hash"],
            loaded_by=row["loaded_by"],
            loaded_at=datetime.fromisoformat(row["loaded_at"]),
        )

    @staticmethod
    def _to_position(row: sqlite3.Row) -> Position:
        maturity = row["maturity_date"]
        return Position(
            position_id=row["position_id"],
            batch_id=row["batch_id"],
            position=PositionInput(
                exposure_ref=row["exposure_ref"],
                holder_lei=row["holder_lei"],
                counterparty_lei=row["counterparty_lei"],
                exposure_type=row["exposure_type"],
                currency=row["currency"],
                amount=Decimal(row["amount"]),
                as_of=date.fromisoformat(row["as_of"]),
                maturity_date=date.fromisoformat(maturity) if maturity else None,
                source_ref=row["source_ref"],
            ),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    # ------------------------------------------------------------------ loading
    def load_batch(self, who: Principal, batch: BatchInput) -> Batch:
        who.require(Permission.EXPOSURE_WRITE)
        tenant, now = who.tenant_id, self._clock.now()
        actor = f"user:{who.user_id}"
        digest = batch.content_hash()
        wanted = {p.holder_lei for p in batch.positions} | {
            p.counterparty_lei for p in batch.positions
        }
        with self._db.transaction():
            missing = wanted - self._registry.existing_leis(tenant, wanted)
            if missing:
                # Only the count is reported: the caller cannot probe which LEIs exist.
                raise ValidationFailure(f"{len(missing)} LEI(s) are not registered in this tenant")
            if self._db.query(
                "SELECT 1 FROM exposure_batches WHERE tenant_id = ? AND content_hash = ?",
                (tenant, digest),
            ):
                raise ConflictError("this batch content was already loaded")
            total = int(
                self._db.query(
                    "SELECT COUNT(*) AS n FROM exposure_positions WHERE tenant_id = ?", (tenant,)
                )[0]["n"]
            )
            if total + len(batch.positions) > MAX_POSITIONS_PER_TENANT:
                raise ValidationFailure("exposure position limit reached")
            batch_id = int(
                self._db.query(
                    "SELECT COALESCE(MAX(batch_id), 0) + 1 AS n FROM exposure_batches "
                    "WHERE tenant_id = ?",
                    (tenant,),
                )[0]["n"]
            )
            first_id = int(
                self._db.query(
                    "SELECT COALESCE(MAX(position_id), 0) + 1 AS n FROM exposure_positions "
                    "WHERE tenant_id = ?",
                    (tenant,),
                )[0]["n"]
            )
            self._db.execute(
                "INSERT INTO exposure_batches (tenant_id, batch_id, source_system, description,"
                " position_count, content_hash, loaded_by, loaded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    batch_id,
                    batch.source_system,
                    batch.description,
                    len(batch.positions),
                    digest,
                    actor,
                    now.isoformat(),
                ),
            )
            for offset, pos in enumerate(batch.positions):
                self._db.execute(
                    "INSERT INTO exposure_positions (tenant_id, position_id, batch_id,"
                    " exposure_ref, holder_lei, counterparty_lei, exposure_type, currency, amount,"
                    " as_of, maturity_date, source_ref, recorded_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        tenant,
                        first_id + offset,
                        batch_id,
                        pos.exposure_ref,
                        pos.holder_lei,
                        pos.counterparty_lei,
                        pos.exposure_type.value,
                        pos.currency,
                        format(pos.amount, "f"),
                        pos.as_of.isoformat(),
                        pos.maturity_date.isoformat() if pos.maturity_date else None,
                        pos.source_ref,
                        now.isoformat(),
                    ),
                )
            self._audit.append(
                tenant,
                actor,
                "exposure.batch_loaded",
                f"batch#{batch_id}",
                {
                    "positions": len(batch.positions),
                    "content_hash": digest,
                    "source_system": batch.source_system,
                },
            )
            return self._batch(tenant, batch_id)

    def _batch(self, tenant: str, batch_id: int) -> Batch:
        rows = self._db.query(
            "SELECT * FROM exposure_batches WHERE tenant_id = ? AND batch_id = ?",
            (tenant, batch_id),
        )
        if not rows:
            raise NotFoundError("exposure batch not found")
        return self._to_batch(rows[0])

    # ------------------------------------------------------------------ reading
    def get_batch(self, who: Principal, batch_id: int) -> Batch:
        who.require(Permission.EXPOSURE_READ)
        return self._batch(who.tenant_id, batch_id)

    def list_batches(self, who: Principal, *, limit: int = 50, after: int = 0) -> list[Batch]:
        who.require(Permission.EXPOSURE_READ)
        limit = max(1, min(limit, _MAX_PAGE))
        rows = self._db.query(
            "SELECT * FROM exposure_batches WHERE tenant_id = ? AND batch_id > ?"
            " ORDER BY batch_id LIMIT ?",
            (who.tenant_id, after, limit),
        )
        return [self._to_batch(r) for r in rows]

    def positions_as_of(
        self,
        who: Principal,
        on: date,
        *,
        holder: str | None = None,
        counterparty: str | None = None,
        max_batch: int | None = None,
    ) -> list[Position]:
        """Current position per exposure_ref on ``on`` (rule X4), optionally limited (rule X5)."""
        who.require(Permission.EXPOSURE_READ)
        rows = self._db.query(
            "SELECT * FROM ("
            " SELECT p.*, ROW_NUMBER() OVER ("
            "  PARTITION BY exposure_ref ORDER BY as_of DESC, position_id DESC) AS rn"
            " FROM exposure_positions p"
            " WHERE tenant_id = ? AND as_of <= ? AND (? IS NULL OR batch_id <= ?)"
            ") WHERE rn = 1 AND (maturity_date IS NULL OR maturity_date >= ?)"
            " AND (? IS NULL OR holder_lei = ?) AND (? IS NULL OR counterparty_lei = ?)"
            " ORDER BY position_id LIMIT ?",
            (
                who.tenant_id,
                on.isoformat(),
                max_batch,
                max_batch,
                on.isoformat(),
                holder,
                holder,
                counterparty,
                counterparty,
                MAX_QUERY_POSITIONS + 1,
            ),
        )
        if len(rows) > MAX_QUERY_POSITIONS:
            raise ValidationFailure("too many positions for one query; narrow it with a filter")
        return [self._to_position(r) for r in rows]
