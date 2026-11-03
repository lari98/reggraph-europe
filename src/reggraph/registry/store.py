"""The entity registry: LEI-keyed entities, licences, ownership links, group queries.

====  ======================================================================================
E1    Entity versions are append-only: an update writes version n+1, history is never edited.
E2    An update identical to the current version is refused (no empty changes).
E3    The tenant always comes from the verified principal, never from the request.
E4    Ownership links are insert-only; the only change allowed is giving an open link an end date.
E5    A new link must not create a cycle, exceed 100 % held in one child, or duplicate a period.
E6    Every change and its audit entry commit in one transaction (both or neither).
====  ======================================================================================
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, NotFoundError, ValidationFailure
from reggraph.registry import groups
from reggraph.registry.lei import validate_lei
from reggraph.registry.models import (
    EntityInput,
    EntityVersion,
    OwnershipInput,
    OwnershipLink,
)
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.security.validation import clean_text
from reggraph.storage.sqlite import Database

MAX_LINKS_PER_TENANT: Final = 100_000
_MAX_PAGE: Final = 200


class EntityStore:
    def __init__(self, db: Database, audit: AuditLog, clock: Clock) -> None:
        self._db = db
        self._audit = audit
        self._clock = clock

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _actor(who: Principal) -> str:
        return f"user:{who.user_id}"

    @staticmethod
    def _to_version(row: sqlite3.Row) -> EntityVersion:
        return EntityVersion(
            lei=row["lei"],
            version_no=row["version_no"],
            entity=EntityInput.model_validate_json(row["payload_json"]),
            content_hash=row["content_hash"],
            change_summary=row["change_summary"],
            changed_by=row["changed_by"],
            changed_at=datetime.fromisoformat(row["changed_at"]),
        )

    @staticmethod
    def _to_link(row: sqlite3.Row) -> OwnershipLink:
        until = row["effective_until"]
        return OwnershipLink(
            link_id=row["link_id"],
            link=OwnershipInput(
                parent_lei=row["parent_lei"],
                child_lei=row["child_lei"],
                percent=Decimal(row["percent"]),
                kind=row["kind"],
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_until=date.fromisoformat(until) if until else None,
            ),
            created_by=row["created_by"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _history(self, tenant: str, lei: str) -> list[EntityVersion]:
        rows = self._db.query(
            "SELECT * FROM entity_versions WHERE tenant_id = ? AND lei = ? ORDER BY version_no",
            (tenant, lei),
        )
        return [self._to_version(r) for r in rows]

    def _current(self, tenant: str, lei: str) -> EntityVersion:
        versions = self._history(tenant, lei)
        if not versions:
            raise NotFoundError("entity not found")
        return versions[-1]

    def _graph(self, tenant: str) -> list[groups.Link]:
        rows = self._db.query("SELECT * FROM ownership_links WHERE tenant_id = ?", (tenant,))
        return [
            groups.Link(
                link_id=r["link_id"],
                parent=r["parent_lei"],
                child=r["child_lei"],
                percent=Decimal(r["percent"]),
                kind=r["kind"],
                effective_from=date.fromisoformat(r["effective_from"]),
                effective_until=(
                    date.fromisoformat(r["effective_until"]) if r["effective_until"] else None
                ),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ entities
    def upsert(self, who: Principal, entity: EntityInput, change_summary: str) -> EntityVersion:
        who.require(Permission.REGISTRY_WRITE)
        summary = clean_text(change_summary, max_len=500)
        tenant, now = who.tenant_id, self._clock.now()
        with self._db.transaction():
            existing = self._history(tenant, entity.lei)
            if existing and existing[-1].content_hash == entity.content_hash():
                raise ConflictError("entity is identical to its current version")
            version_no = existing[-1].version_no + 1 if existing else 1
            before = existing[-1].entity.model_dump(mode="json") if existing else {}
            after = entity.model_dump(mode="json")
            changed = summarise_changes(before, after)
            self._db.execute(
                "INSERT INTO entity_versions (tenant_id, lei, version_no, legal_name, entity_type,"
                " jurisdiction, status, payload_json, content_hash, change_summary, changed_by,"
                " changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    entity.lei,
                    version_no,
                    entity.legal_name,
                    entity.entity_type.value,
                    entity.jurisdiction,
                    entity.status.value,
                    entity.model_dump_json(),
                    entity.content_hash(),
                    summary,
                    self._actor(who),
                    now.isoformat(),
                ),
            )
            self._audit.append(
                tenant,
                self._actor(who),
                "entity.created" if version_no == 1 else "entity.updated",
                f"{entity.lei}@{version_no}",
                {"changed_fields": changed, "content_hash": entity.content_hash()},
            )
            return self._current(tenant, entity.lei)

    def get(self, who: Principal, lei: str) -> EntityVersion:
        who.require(Permission.REGISTRY_READ)
        return self._current(who.tenant_id, validate_lei(lei))

    def history(self, who: Principal, lei: str) -> list[EntityVersion]:
        who.require(Permission.REGISTRY_READ)
        versions = self._history(who.tenant_id, validate_lei(lei))
        if not versions:
            raise NotFoundError("entity not found")
        return versions

    def list_entities(
        self,
        who: Principal,
        *,
        jurisdiction: str | None = None,
        entity_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        after: str = "",
    ) -> list[EntityVersion]:
        """Current version of each entity, ordered by LEI, with simple filters and a cursor."""
        who.require(Permission.REGISTRY_READ)
        limit = max(1, min(limit, _MAX_PAGE))
        tenant = who.tenant_id
        rows = self._db.query(
            "SELECT e.* FROM entity_versions e JOIN ("
            " SELECT lei, MAX(version_no) AS v FROM entity_versions WHERE tenant_id = ?"
            " GROUP BY lei) m ON e.lei = m.lei AND e.version_no = m.v"
            " WHERE e.tenant_id = ? AND e.lei > ?"
            " AND (? IS NULL OR e.jurisdiction = ?)"
            " AND (? IS NULL OR e.entity_type = ?)"
            " AND (? IS NULL OR e.status = ?)"
            " ORDER BY e.lei LIMIT ?",
            (
                tenant,
                tenant,
                after,
                jurisdiction,
                jurisdiction,
                entity_type,
                entity_type,
                status,
                status,
                limit,
            ),
        )
        return [self._to_version(r) for r in rows]

    # ------------------------------------------------------------------ ownership
    def link(self, who: Principal, ownership: OwnershipInput) -> OwnershipLink:
        who.require(Permission.REGISTRY_WRITE)
        tenant, now = who.tenant_id, self._clock.now()
        with self._db.transaction():
            self._current(tenant, ownership.parent_lei)  # both ends must exist in this tenant
            self._current(tenant, ownership.child_lei)
            graph = self._graph(tenant)
            if len(graph) >= MAX_LINKS_PER_TENANT:
                raise ValidationFailure("ownership link limit reached")
            start, until = ownership.effective_from, ownership.effective_until
            into_child = [lk for lk in graph if lk.child == ownership.child_lei]
            if any(
                lk.parent == ownership.parent_lei and lk.overlaps(start, until) for lk in into_child
            ):
                raise ConflictError("this parent already holds the child in an overlapping period")
            if groups.would_create_cycle(
                graph, ownership.parent_lei, ownership.child_lei, start, until
            ):
                raise ConflictError("this link would create an ownership cycle")
            candidate = groups.Link(
                0,
                ownership.parent_lei,
                ownership.child_lei,
                ownership.percent,
                ownership.kind.value,
                start,
                until,
            )
            if groups.max_concurrent_percent(into_child, candidate) > 100:
                raise ConflictError("holdings in the child would exceed 100 percent")
            next_id = int(
                self._db.query(
                    "SELECT COALESCE(MAX(link_id), 0) + 1 AS n FROM ownership_links "
                    "WHERE tenant_id = ?",
                    (tenant,),
                )[0]["n"]
            )
            self._db.execute(
                "INSERT INTO ownership_links (tenant_id, link_id, parent_lei, child_lei, percent,"
                " kind, effective_from, effective_until, created_by, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant,
                    next_id,
                    ownership.parent_lei,
                    ownership.child_lei,
                    format(ownership.percent, "f"),
                    ownership.kind.value,
                    start.isoformat(),
                    until.isoformat() if until else None,
                    self._actor(who),
                    now.isoformat(),
                ),
            )
            self._audit.append(
                tenant,
                self._actor(who),
                "ownership.created",
                f"{ownership.parent_lei}>{ownership.child_lei}#{next_id}",
                {
                    "percent": format(ownership.percent, "f"),
                    "kind": ownership.kind.value,
                    "from": start.isoformat(),
                    "until": until.isoformat() if until else None,
                },
            )
            return self._link_by_id(tenant, next_id)

    def _link_by_id(self, tenant: str, link_id: int) -> OwnershipLink:
        rows = self._db.query(
            "SELECT * FROM ownership_links WHERE tenant_id = ? AND link_id = ?", (tenant, link_id)
        )
        if not rows:
            raise NotFoundError("ownership link not found")
        return self._to_link(rows[0])

    def end_link(self, who: Principal, link_id: int, until: date) -> OwnershipLink:
        """Give an open link its last day. The link itself is never edited or removed."""
        who.require(Permission.REGISTRY_WRITE)
        tenant = who.tenant_id
        with self._db.transaction():
            current = self._link_by_id(tenant, link_id)
            if current.link.effective_until is not None:
                raise ConflictError("ownership link already ended")
            if until < current.link.effective_from:
                raise ValidationFailure("end date precedes the start of the link")
            self._db.execute(
                "UPDATE ownership_links SET effective_until = ? "
                "WHERE tenant_id = ? AND link_id = ? AND effective_until IS NULL",
                (until.isoformat(), tenant, link_id),
            )
            self._audit.append(
                tenant,
                self._actor(who),
                "ownership.ended",
                f"{current.link.parent_lei}>{current.link.child_lei}#{link_id}",
                {"until": until.isoformat()},
            )
            return self._link_by_id(tenant, link_id)

    def links_of(self, who: Principal, lei: str, on: date) -> dict[str, list[OwnershipLink]]:
        """Links touching ``lei`` that apply on ``on``: who owns it, and what it owns."""
        who.require(Permission.REGISTRY_READ)
        key = validate_lei(lei)
        self._current(who.tenant_id, key)
        rows = self._db.query(
            "SELECT * FROM ownership_links WHERE tenant_id = ?"
            " AND (parent_lei = ? OR child_lei = ?) ORDER BY link_id",
            (who.tenant_id, key, key),
        )
        result: dict[str, list[OwnershipLink]] = {"parents": [], "children": []}
        for row in rows:
            item = self._to_link(row)
            lk = item.link
            active = lk.effective_from <= on and (
                lk.effective_until is None or on <= lk.effective_until
            )
            if not active:
                continue
            (result["children"] if lk.parent_lei == key else result["parents"]).append(item)
        return result

    # ------------------------------------------------------------------ group queries
    def group(self, who: Principal, lei: str, on: date) -> groups.GroupView:
        who.require(Permission.REGISTRY_READ)
        key = validate_lei(lei)
        self._current(who.tenant_id, key)
        return groups.controlled_group(self._graph(who.tenant_id), key, on)

    def ultimate_parent(self, who: Principal, lei: str, on: date) -> EntityVersion:
        who.require(Permission.REGISTRY_READ)
        key = validate_lei(lei)
        self._current(who.tenant_id, key)
        top = groups.ultimate_parent(self._graph(who.tenant_id), key, on)
        return self._current(who.tenant_id, top)

    def has_subsidiaries(self, who: Principal, lei: str, on: date) -> bool:
        return bool(self.group(who, lei, on).members)


def summarise_changes(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Names of top-level fields that differ (kept public for tests and the API layer)."""
    return sorted(k for k in after if before.get(k) != after[k])
