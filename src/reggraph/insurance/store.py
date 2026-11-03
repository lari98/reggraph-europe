"""Insurance contract storage (versioned, audited).

====  ======================================================================================
I1    Contract versions are append-only; an update writes version n+1.
I2    An update identical to the current version is refused.
I3    Insurer and insured must be registered in the caller's tenant; the insurer must be an
      insurer or reinsurer (a reinsurance contract needs a reinsurer or insurer as taker).
I4    ``reinsures_ref`` must name an existing contract of the same tenant.
I5    Contract and audit entry commit in one transaction.
====  ======================================================================================
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, NotFoundError, ValidationFailure
from reggraph.insurance.models import ContractInput, ContractVersion
from reggraph.registry.lei import validate_lei
from reggraph.registry.store import EntityStore
from reggraph.regulation.models import EntityType
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.security.validation import clean_text, ensure_identifier
from reggraph.storage.sqlite import Database

_MAX_PAGE = 200
_INSURER_TYPES = {EntityType.INSURER, EntityType.REINSURER}


class ContractStore:
    def __init__(self, db: Database, audit: AuditLog, clock: Clock, registry: EntityStore) -> None:
        self._db = db
        self._audit = audit
        self._clock = clock
        self._registry = registry

    @staticmethod
    def _to_version(row: sqlite3.Row) -> ContractVersion:
        return ContractVersion(
            contract_ref=row["contract_ref"],
            version_no=row["version_no"],
            contract=ContractInput.model_validate_json(row["payload_json"]),
            content_hash=row["content_hash"],
            change_summary=row["change_summary"],
            changed_by=row["changed_by"],
            changed_at=datetime.fromisoformat(row["changed_at"]),
        )

    def _history(self, tenant: str, ref: str) -> list[ContractVersion]:
        rows = self._db.query(
            "SELECT * FROM insurance_contract_versions WHERE tenant_id = ? AND contract_ref = ?"
            " ORDER BY version_no",
            (tenant, ref),
        )
        return [self._to_version(r) for r in rows]

    def _insurer_type(self, tenant: str, lei: str) -> str:
        rows = self._db.query(
            "SELECT entity_type FROM entity_versions WHERE tenant_id = ? AND lei = ?"
            " ORDER BY version_no DESC LIMIT 1",
            (tenant, lei),
        )
        return str(rows[0]["entity_type"]) if rows else ""

    def upsert(
        self, who: Principal, contract: ContractInput, change_summary: str
    ) -> ContractVersion:
        who.require(Permission.INSURANCE_WRITE)
        summary = clean_text(change_summary, max_len=500)
        tenant, now = who.tenant_id, self._clock.now()
        actor = f"user:{who.user_id}"
        with self._db.transaction():
            wanted = {contract.insurer_lei, contract.insured_lei}
            if wanted - self._registry.existing_leis(tenant, wanted):
                raise ValidationFailure("insurer and insured must be registered in this tenant")
            if self._insurer_type(tenant, contract.insurer_lei) not in _INSURER_TYPES:
                raise ValidationFailure("the insurer must be registered as an insurer or reinsurer")
            if contract.reinsures_ref and not self._history(tenant, contract.reinsures_ref):
                raise ValidationFailure("reinsures_ref names an unknown contract")
            existing = self._history(tenant, contract.contract_ref)
            if existing and existing[-1].content_hash == contract.content_hash():
                raise ConflictError("contract is identical to its current version")
            version_no = existing[-1].version_no + 1 if existing else 1
            before = existing[-1].contract.model_dump(mode="json") if existing else {}
            after = contract.model_dump(mode="json")
            changed = sorted(k for k in after if before.get(k) != after[k])
            self._db.execute(
                "INSERT INTO insurance_contract_versions (tenant_id, contract_ref, version_no,"
                " insurer_lei, insured_lei, line, payload_json, content_hash, change_summary,"
                " changed_by, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    contract.contract_ref,
                    version_no,
                    contract.insurer_lei,
                    contract.insured_lei,
                    contract.line.value,
                    contract.model_dump_json(),
                    contract.content_hash(),
                    summary,
                    actor,
                    now.isoformat(),
                ),
            )
            self._audit.append(
                tenant,
                actor,
                "contract.created" if version_no == 1 else "contract.updated",
                f"{contract.contract_ref}@{version_no}",
                {"changed_fields": changed, "content_hash": contract.content_hash()},
            )
            return self._history(tenant, contract.contract_ref)[-1]

    def get(self, who: Principal, ref: str) -> ContractVersion:
        who.require(Permission.INSURANCE_READ)
        versions = self._history(who.tenant_id, ensure_identifier(ref, "contract reference"))
        if not versions:
            raise NotFoundError("contract not found")
        return versions[-1]

    def history(self, who: Principal, ref: str) -> list[ContractVersion]:
        who.require(Permission.INSURANCE_READ)
        versions = self._history(who.tenant_id, ensure_identifier(ref, "contract reference"))
        if not versions:
            raise NotFoundError("contract not found")
        return versions

    def list_contracts(
        self,
        who: Principal,
        *,
        insurer: str | None = None,
        insured: str | None = None,
        line: str | None = None,
        limit: int = 50,
        after: str = "",
    ) -> list[ContractVersion]:
        """Current version of each contract, ordered by reference, with filters and a cursor."""
        who.require(Permission.INSURANCE_READ)
        limit = max(1, min(limit, _MAX_PAGE))
        tenant = who.tenant_id
        insurer_key = validate_lei(insurer) if insurer else None
        insured_key = validate_lei(insured) if insured else None
        rows = self._db.query(
            "SELECT c.* FROM insurance_contract_versions c JOIN ("
            " SELECT contract_ref, MAX(version_no) AS v FROM insurance_contract_versions"
            " WHERE tenant_id = ? GROUP BY contract_ref) m"
            " ON c.contract_ref = m.contract_ref AND c.version_no = m.v"
            " WHERE c.tenant_id = ? AND c.contract_ref > ?"
            " AND (? IS NULL OR c.insurer_lei = ?) AND (? IS NULL OR c.insured_lei = ?)"
            " AND (? IS NULL OR c.line = ?) ORDER BY c.contract_ref LIMIT ?",
            (
                tenant,
                tenant,
                after,
                insurer_key,
                insurer_key,
                insured_key,
                insured_key,
                line,
                line,
                limit,
            ),
        )
        return [self._to_version(r) for r in rows]

    def all_current(self, who: Principal) -> list[ContractVersion]:
        """Every current contract of the tenant (used by the connectivity views)."""
        who.require(Permission.INSURANCE_READ)
        out: list[ContractVersion] = []
        after = ""
        while True:
            page = self.list_contracts(who, limit=_MAX_PAGE, after=after)
            out.extend(page)
            if len(page) < _MAX_PAGE:
                return out
            after = page[-1].contract_ref
