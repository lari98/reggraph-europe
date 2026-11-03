"""Tamper-evident audit trail (hash chain, one chain per tenant).

Each entry stores the hash of the previous entry. Changing, reordering or deleting an entry
in the middle breaks the chain and ``verify`` reports the first broken position.

Limit (stated honestly): deleting the *last* entries cannot be detected from inside the
database alone. ``head`` returns the latest position and hash; production copies it to
write-once storage (S3 Object Lock) so truncation is detectable too (Phase 4).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final

from reggraph.core.clock import Clock
from reggraph.storage.base import Db

GENESIS: Final = "0" * 64


@dataclass(frozen=True, slots=True)
class AuditEntry:
    n: int
    ts: str
    actor: str
    action: str
    subject: str
    detail: dict[str, Any]
    prev_hash: str
    entry_hash: str


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    entries: int
    first_bad: int | None  # position (n) of the first entry that fails, if any


def _canonical(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(
    prev_hash: str,
    tenant_id: str,
    n: int,
    ts: str,
    actor: str,
    action: str,
    subject: str,
    detail_json: str,
) -> str:
    body = _canonical([prev_hash, tenant_id, n, ts, actor, action, subject, detail_json])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, db: Db, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def append(
        self, tenant_id: str, actor: str, action: str, subject: str, detail: dict[str, Any]
    ) -> AuditEntry:
        """Add an entry. Joins the caller's transaction so state and audit commit together."""
        detail_json = _canonical(detail)
        ts = self._clock.now().isoformat()
        with self._db.transaction():
            last = self._db.query(
                "SELECT n, entry_hash FROM audit_log WHERE tenant_id = ? ORDER BY n DESC LIMIT 1",
                (tenant_id,),
            )
            n = int(last[0]["n"]) + 1 if last else 1
            prev = str(last[0]["entry_hash"]) if last else GENESIS
            digest = _hash(prev, tenant_id, n, ts, actor, action, subject, detail_json)
            self._db.execute(
                "INSERT INTO audit_log (tenant_id, n, ts, actor, action, subject, detail_json, "
                "prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, n, ts, actor, action, subject, detail_json, prev, digest),
            )
        return AuditEntry(n, ts, actor, action, subject, dict(detail), prev, digest)

    def entries(self, tenant_id: str, limit: int = 100, after: int = 0) -> list[AuditEntry]:
        limit = max(1, min(limit, 500))
        rows = self._db.query(
            "SELECT * FROM audit_log WHERE tenant_id = ? AND n > ? ORDER BY n ASC LIMIT ?",
            (tenant_id, after, limit),
        )
        return [
            AuditEntry(
                int(r["n"]),
                r["ts"],
                r["actor"],
                r["action"],
                r["subject"],
                json.loads(r["detail_json"]),
                r["prev_hash"],
                r["entry_hash"],
            )
            for r in rows
        ]

    def head(self, tenant_id: str) -> tuple[int, str]:
        """Latest position and hash. Publish this externally to detect truncation."""
        rows = self._db.query(
            "SELECT n, entry_hash FROM audit_log WHERE tenant_id = ? ORDER BY n DESC LIMIT 1",
            (tenant_id,),
        )
        return (int(rows[0]["n"]), str(rows[0]["entry_hash"])) if rows else (0, GENESIS)

    def verify(self, tenant_id: str) -> VerifyResult:
        rows = self._db.query(
            "SELECT * FROM audit_log WHERE tenant_id = ? ORDER BY n ASC", (tenant_id,)
        )
        prev = GENESIS
        for expected_n, row in enumerate(rows, start=1):
            n = int(row["n"])
            recomputed = _hash(
                prev,
                tenant_id,
                n,
                row["ts"],
                row["actor"],
                row["action"],
                row["subject"],
                row["detail_json"],
            )
            if n != expected_n or row["prev_hash"] != prev or row["entry_hash"] != recomputed:
                return VerifyResult(False, len(rows), n)
            prev = row["entry_hash"]
        return VerifyResult(True, len(rows), None)
