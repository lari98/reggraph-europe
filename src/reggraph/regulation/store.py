"""The versioned rule store: propose, approve, reject, withdraw, ingest, query.

Life of a rule version::

    propose / ingest ──► DRAFT ──approve──► APPROVED ──newer approved──► SUPERSEDED
                           │                   │
                           └──reject──► REJECTED └──withdraw──► WITHDRAWN

Rules enforced here
-------------------
====  =====================================================================================
R1    Versions are append-only. Content never changes; only the review status moves.
R2    At most one open draft per rule key (no competing edits).
R3    A draft identical to the current approved version is refused (no empty changes).
R4    Four-eyes: the person who proposed a version cannot approve it.
R5    The tenant always comes from the verified principal, never from the request.
R6    Machine ingestion only ever creates DRAFTs plus a review task for the control owner.
R7    State change and audit entry are written in one transaction (both or neither).
====  =====================================================================================
"""

from __future__ import annotations

from datetime import date, datetime

from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailure
from reggraph.jurisdictions import get_jurisdiction
from reggraph.regulation.applicability import ApplicableRule, select_applicable
from reggraph.regulation.models import (
    ConsolidationBasis,
    EntityType,
    ReviewTask,
    RuleInput,
    RuleStatus,
    RuleVersion,
)
from reggraph.regulation.versioning import diff, is_material
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.security.validation import clean_text, ensure_rule_key
from reggraph.storage.base import Db, Row


class RuleStore:
    def __init__(self, db: Db, audit: AuditLog, clock: Clock) -> None:
        self._db = db
        self._audit = audit
        self._clock = clock

    # ------------------------------------------------------------------ reading helpers
    @staticmethod
    def _to_version(r: Row) -> RuleVersion:
        return RuleVersion(
            rule_key=r["rule_key"],
            version_no=r["version_no"],
            status=RuleStatus(r["status"]),
            rule=RuleInput.model_validate_json(r["payload_json"]),
            content_hash=r["content_hash"],
            change_summary=r["change_summary"],
            source=r["source"],
            proposed_by=r["proposed_by"],
            proposed_at=datetime.fromisoformat(r["proposed_at"]),
            decided_by=r["decided_by"],
            decided_at=datetime.fromisoformat(r["decided_at"]) if r["decided_at"] else None,
            decision_note=r["decision_note"],
        )

    def _versions(self, tenant_id: str, rule_key: str) -> list[RuleVersion]:
        rows = self._db.query(
            "SELECT * FROM rule_versions WHERE tenant_id = ? AND rule_key = ? "
            "ORDER BY version_no ASC",
            (tenant_id, rule_key),
        )
        return [self._to_version(row) for row in rows]

    def _actor(self, who: Principal) -> str:
        return f"user:{who.user_id}"

    # ------------------------------------------------------------------ writing
    def propose(self, who: Principal, rule: RuleInput, change_summary: str) -> RuleVersion:
        who.require(Permission.RULES_PROPOSE)
        version, _ = self._create_draft(who, rule, change_summary, source="manual")
        return version

    def ingest(
        self, who: Principal, rule: RuleInput, source_ref: str
    ) -> tuple[RuleVersion, ReviewTask]:
        """Machine-detected candidate. Creates a DRAFT and a review task, never an approval."""
        who.require(Permission.RULES_INGEST)
        summary = "ingested: " + clean_text(source_ref, max_len=300)
        return self._create_draft(who, rule, summary, source="ingestion")

    def _create_draft(
        self, who: Principal, rule: RuleInput, change_summary: str, *, source: str
    ) -> tuple[RuleVersion, ReviewTask]:
        summary = clean_text(change_summary, max_len=500)
        tenant = who.tenant_id
        now = self._clock.now()
        with self._db.transaction():
            existing = self._versions(tenant, rule.rule_key)
            if any(v.status is RuleStatus.DRAFT for v in existing):
                raise ConflictError("an open draft already exists for this rule")
            approved = [v for v in existing if v.status is RuleStatus.APPROVED]
            current = approved[-1] if approved else None
            if current is not None and current.content_hash == rule.content_hash():
                raise ConflictError("draft is identical to the approved version")
            changes = diff(current.rule if current else None, rule)
            version_no = (existing[-1].version_no + 1) if existing else 1
            self._db.execute(
                "INSERT INTO rule_versions (tenant_id, rule_key, version_no, status, payload_json,"
                " content_hash, change_summary, source, proposed_by, proposed_at)"
                " VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    rule.rule_key,
                    version_no,
                    rule.model_dump_json(),
                    rule.content_hash(),
                    summary,
                    source,
                    self._actor(who),
                    now.isoformat(),
                ),
            )
            task_rows = self._db.query(
                "SELECT COALESCE(MAX(task_id), 0) + 1 AS next FROM review_tasks "
                "WHERE tenant_id = ?",
                (tenant,),
            )
            task_id = int(task_rows[0]["next"])
            self._db.execute(
                "INSERT INTO review_tasks (tenant_id, task_id, rule_key, version_no, owner, status,"
                " created_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                (tenant, task_id, rule.rule_key, version_no, rule.control_owner, now.isoformat()),
            )
            self._audit.append(
                tenant,
                self._actor(who),
                "rule.draft_created",
                f"{rule.rule_key}@{version_no}",
                {
                    "source": source,
                    "material": is_material(changes),
                    "changed_fields": [c.field for c in changes],
                    "content_hash": rule.content_hash(),
                },
            )
            version = self._versions(tenant, rule.rule_key)[-1]
        task = ReviewTask(
            task_id=task_id,
            rule_key=rule.rule_key,
            version_no=version_no,
            owner=rule.control_owner,
            status="open",
            created_at=now,
        )
        return version, task

    def approve(
        self, who: Principal, rule_key: str, version_no: int, note: str = ""
    ) -> RuleVersion:
        who.require(Permission.RULES_APPROVE)
        key = ensure_rule_key(rule_key)
        text = clean_text(note, max_len=500, allow_empty=True)
        now = self._clock.now().isoformat()
        with self._db.transaction():
            target = self._get_version(who.tenant_id, key, version_no)
            if target.status is not RuleStatus.DRAFT:
                raise ConflictError("only a draft can be approved")
            if target.proposed_by == self._actor(who):
                raise ForbiddenError("four-eyes rule: you cannot approve your own proposal")
            changed = self._db.execute(
                "UPDATE rule_versions SET status = 'approved', decided_by = ?, decided_at = ?,"
                " decision_note = ? WHERE tenant_id = ? AND rule_key = ? AND version_no = ?"
                " AND status = 'draft'",
                (self._actor(who), now, text, who.tenant_id, key, version_no),
            )
            if changed != 1:  # lost a race with another decision
                raise ConflictError("version is no longer a draft")
            self._db.execute(
                "UPDATE rule_versions SET status = 'superseded' WHERE tenant_id = ? AND"
                " rule_key = ? AND status = 'approved' AND version_no <> ?",
                (who.tenant_id, key, version_no),
            )
            self._close_tasks(who.tenant_id, key, version_no)
            self._audit.append(
                who.tenant_id,
                self._actor(who),
                "rule.approved",
                f"{key}@{version_no}",
                {"note": text, "proposed_by": target.proposed_by},
            )
            return self._get_version(who.tenant_id, key, version_no)

    def reject(self, who: Principal, rule_key: str, version_no: int, note: str = "") -> RuleVersion:
        who.require(Permission.RULES_APPROVE)
        key = ensure_rule_key(rule_key)
        text = clean_text(note, max_len=500, allow_empty=True)
        now = self._clock.now().isoformat()
        with self._db.transaction():
            target = self._get_version(who.tenant_id, key, version_no)
            if target.status is not RuleStatus.DRAFT:
                raise ConflictError("only a draft can be rejected")
            self._db.execute(
                "UPDATE rule_versions SET status = 'rejected', decided_by = ?, decided_at = ?,"
                " decision_note = ? WHERE tenant_id = ? AND rule_key = ? AND version_no = ?"
                " AND status = 'draft'",
                (self._actor(who), now, text, who.tenant_id, key, version_no),
            )
            self._close_tasks(who.tenant_id, key, version_no)
            self._audit.append(
                who.tenant_id,
                self._actor(who),
                "rule.rejected",
                f"{key}@{version_no}",
                {"note": text},
            )
            return self._get_version(who.tenant_id, key, version_no)

    def withdraw(self, who: Principal, rule_key: str, note: str = "") -> RuleVersion:
        """Logical delete: the approved version is marked withdrawn. History is kept (R1)."""
        who.require(Permission.RULES_WITHDRAW)
        key = ensure_rule_key(rule_key)
        text = clean_text(note, max_len=500, allow_empty=True)
        now = self._clock.now().isoformat()
        with self._db.transaction():
            approved = [
                v for v in self._versions(who.tenant_id, key) if v.status is RuleStatus.APPROVED
            ]
            if not approved:
                raise NotFoundError("no approved version to withdraw")
            target = approved[-1]
            self._db.execute(
                "UPDATE rule_versions SET status = 'withdrawn', decided_by = ?, decided_at = ?,"
                " decision_note = ? WHERE tenant_id = ? AND rule_key = ? AND version_no = ?",
                (self._actor(who), now, text, who.tenant_id, key, target.version_no),
            )
            self._audit.append(
                who.tenant_id,
                self._actor(who),
                "rule.withdrawn",
                f"{key}@{target.version_no}",
                {"note": text},
            )
            return self._get_version(who.tenant_id, key, target.version_no)

    def _close_tasks(self, tenant_id: str, key: str, version_no: int) -> None:
        self._db.execute(
            "UPDATE review_tasks SET status = 'done' WHERE tenant_id = ? AND rule_key = ?"
            " AND version_no = ?",
            (tenant_id, key, version_no),
        )

    # ------------------------------------------------------------------ reading
    def _get_version(self, tenant_id: str, key: str, version_no: int) -> RuleVersion:
        rows = self._db.query(
            "SELECT * FROM rule_versions WHERE tenant_id = ? AND rule_key = ? AND version_no = ?",
            (tenant_id, key, version_no),
        )
        if not rows:
            raise NotFoundError("rule version not found")  # same answer for "other tenant's"
        return self._to_version(rows[0])

    def history(self, who: Principal, rule_key: str) -> list[RuleVersion]:
        who.require(Permission.RULES_READ)
        versions = self._versions(who.tenant_id, ensure_rule_key(rule_key))
        if not versions:
            raise NotFoundError("rule not found")
        return versions

    def current_rules(self, who: Principal) -> list[RuleVersion]:
        """Latest approved version of every rule."""
        who.require(Permission.RULES_READ)
        rows = self._db.query(
            "SELECT * FROM rule_versions WHERE tenant_id = ? AND status = 'approved'"
            " ORDER BY rule_key ASC",
            (who.tenant_id,),
        )
        return [self._to_version(row) for row in rows]

    def applicable(
        self,
        who: Principal,
        jurisdiction: str,
        entity_type: EntityType,
        basis: ConsolidationBasis,
        on: date,
    ) -> list[ApplicableRule]:
        who.require(Permission.RULES_READ)
        jur = get_jurisdiction(jurisdiction)
        if jur is None:
            raise ValidationFailure("unknown jurisdiction")
        rows = self._db.query(
            "SELECT * FROM rule_versions WHERE tenant_id = ? "
            "AND status IN ('approved','superseded')",
            (who.tenant_id,),
        )
        return select_applicable(
            (self._to_version(row) for row in rows), jur.code, entity_type, basis, on
        )

    def open_tasks(self, who: Principal) -> list[ReviewTask]:
        who.require(Permission.TASKS_READ)
        rows = self._db.query(
            "SELECT * FROM review_tasks WHERE tenant_id = ? AND status = 'open' ORDER BY task_id",
            (who.tenant_id,),
        )
        return [
            ReviewTask(
                task_id=r["task_id"],
                rule_key=r["rule_key"],
                version_no=r["version_no"],
                owner=r["owner"],
                status=r["status"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
