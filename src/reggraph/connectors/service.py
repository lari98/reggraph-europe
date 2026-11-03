"""Connector runs: pull public data in, record what happened, never trust the source.

Two connectors exist in Phase 5:

* ``ecb_rates``    stores the ECB's daily euro reference rates through ``RateBook`` (so every
                   rule of the rate book still applies: no future dates, no overwriting).
* ``eurlex_watch`` turns new items of an EUR-Lex feed into *alerts* for a person to review.
                   It never creates or changes a rule.

=====  ===============================================================================
C1     Only a tenant admin may run a connector (``connectors:run``); reading history
       needs ``connectors:read``. The tenant always comes from the verified token.
C2     URLs come from start-up settings, never from a request, and pass the fetcher's
       allow-list, so a caller cannot make the server fetch an address of their choosing.
C3     At most one run per connector every ``MIN_INTERVAL`` (default 5 minutes) per tenant:
       a caller cannot turn the platform into a hammer against a public service.
C4     Every run, successful or not, leaves an immutable row and an audit entry.
C5     A failed run stores only a short error code, never response bodies or URLs with
       credentials.
C6     Rates older than ``MAX_RATE_AGE_DAYS`` are refused as stale; unsupported currencies are
       skipped and counted, not guessed.
C7     Re-running for a day that is already stored is "unchanged", not an error.
C8     Alerts are unique per (tenant, source, item), content is immutable, and only
       the acknowledgement fields can change.
=====  ===============================================================================
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from reggraph.connectors.ecb import parse_ecb_rates
from reggraph.connectors.eurlex import parse_feed
from reggraph.core.clock import Clock
from reggraph.core.errors import ConflictError, NotFoundError, ValidationFailure
from reggraph.currency import get_currency
from reggraph.fx import RateBook
from reggraph.netio import Fetcher, UpstreamError
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Permission, Principal
from reggraph.storage import Db

log = logging.getLogger("reggraph.connectors")

MIN_INTERVAL: Final = timedelta(minutes=5)
MAX_RATE_AGE_DAYS: Final = 14
_MAX_PAGE: Final = 200
XML_TYPES: Final = ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml")

Connector = Literal["ecb_rates", "eurlex_watch"]
RunStatus = Literal["succeeded", "unchanged", "failed"]


class RunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: int
    connector: Connector
    status: RunStatus
    started_by: str
    started_at: str
    summary: dict[str, Any]


class AlertRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    alert_id: int
    source: str
    item_key: str
    title: str
    url: str
    published: str | None
    status: Literal["open", "acknowledged"]
    created_at: str
    acknowledged_by: str | None
    acknowledged_at: str | None


class ConnectorService:
    def __init__(
        self,
        db: Db,
        audit: AuditLog,
        clock: Clock,
        ratebook: RateBook,
        fetcher: Fetcher,
        *,
        ecb_url: str,
        eurlex_url: str,
        min_interval: timedelta = MIN_INTERVAL,
        offline: bool = False,
    ) -> None:
        self._offline = offline
        self._db, self._audit, self._clock = db, audit, clock
        self._rates, self._fetcher = ratebook, fetcher
        self._urls = {"ecb_rates": ecb_url, "eurlex_watch": eurlex_url}
        self._min_interval = min_interval
        self._runners: dict[str, Callable[[Principal], tuple[RunStatus, dict[str, Any]]]] = {
            "ecb_rates": self._ecb,
            "eurlex_watch": self._eurlex,
        }

    # ------------------------------------------------------------------ running
    def run(self, who: Principal, connector: str) -> RunRecord:
        who.require(Permission.CONNECTORS_RUN)
        if self._offline:
            raise ValidationFailure("offline mode: connectors are switched off")
        runner = self._runners.get(connector)
        if runner is None:
            raise NotFoundError("unknown connector")
        if not self._urls[connector]:
            raise ValidationFailure("this connector is not configured")
        now = self._clock.now()
        last = self._db.query(
            "SELECT started_at FROM connector_runs WHERE tenant_id = ? AND connector = ?"
            " ORDER BY run_id DESC LIMIT 1",
            (who.tenant_id, connector),
        )
        if last and now - datetime.fromisoformat(last[0]["started_at"]) < self._min_interval:
            raise ConflictError("connector was run a moment ago")
        try:
            status, summary = runner(who)
        except (UpstreamError, ValidationFailure, ConflictError) as exc:
            log.warning("connector failed connector=%s code=%s", connector, exc.code)
            status, summary = "failed", {"error": exc.code}
        return self._record(who, connector, status, summary, now)

    def _ecb(self, who: Principal) -> tuple[RunStatus, dict[str, Any]]:
        body = self._fetcher.get(self._urls["ecb_rates"], XML_TYPES).body
        as_of, rates = parse_ecb_rates(body)
        if (self._clock.now().date() - as_of).days > MAX_RATE_AGE_DAYS:
            raise ValidationFailure("rate file is stale")
        usable: dict[str, str] = {}
        for code, rate in rates.items():
            cur = get_currency(code)
            if cur is not None and cur.active and cur.code != "EUR":
                usable[cur.code] = rate
        skipped = len(rates) - len(usable)
        if not usable:
            raise ValidationFailure("no supported currency in the rate file")
        try:
            stored = self._rates.put(who, as_of, "ECB reference rates", usable)
        except ConflictError:
            return "unchanged", {"as_of": as_of.isoformat(), "skipped": skipped}
        return "succeeded", {"as_of": as_of.isoformat(), "stored": stored, "skipped": skipped}

    def _eurlex(self, who: Principal) -> tuple[RunStatus, dict[str, Any]]:
        body = self._fetcher.get(self._urls["eurlex_watch"], XML_TYPES).body
        items, skipped = parse_feed(body)
        now = self._clock.now().isoformat()
        created = 0
        with self._db.transaction():
            for item in items:
                if self._db.query(
                    "SELECT 1 FROM source_alerts WHERE tenant_id = ? AND source = 'eurlex'"
                    " AND item_key = ?",
                    (who.tenant_id, item.key),
                ):
                    continue
                row = self._db.query(
                    "SELECT COALESCE(MAX(alert_id), 0) + 1 AS n FROM source_alerts"
                    " WHERE tenant_id = ?",
                    (who.tenant_id,),
                )
                self._db.execute(
                    "INSERT INTO source_alerts (tenant_id, alert_id, source, item_key, title,"
                    " url, published, status, created_at) VALUES (?, ?, 'eurlex', ?, ?, ?, ?,"
                    " 'open', ?)",
                    (
                        who.tenant_id,
                        int(row[0]["n"]),
                        item.key,
                        item.title,
                        item.url,
                        item.published,
                        now,
                    ),
                )
                created += 1
        status: RunStatus = "succeeded" if created else "unchanged"
        return status, {"items_seen": len(items), "new_alerts": created, "skipped": skipped}

    def _record(
        self,
        who: Principal,
        connector: str,
        status: RunStatus,
        summary: dict[str, Any],
        began: datetime,
    ) -> RunRecord:
        started = began.isoformat()
        with self._db.transaction():
            row = self._db.query(
                "SELECT COALESCE(MAX(run_id), 0) + 1 AS n FROM connector_runs WHERE tenant_id = ?",
                (who.tenant_id,),
            )
            run_id = int(row[0]["n"])
            self._db.execute(
                "INSERT INTO connector_runs (tenant_id, run_id, connector, status, started_by,"
                " started_at, summary_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    who.tenant_id,
                    run_id,
                    connector,
                    status,
                    f"user:{who.user_id}",
                    started,
                    json.dumps(summary, sort_keys=True, separators=(",", ":")),
                ),
            )
            self._audit.append(
                who.tenant_id,
                f"user:{who.user_id}",
                "connector.run",
                connector,
                {"run_id": run_id, "status": status, **summary},
            )
        return RunRecord(
            run_id=run_id,
            connector=connector,  # type: ignore[arg-type]
            status=status,
            started_by=f"user:{who.user_id}",
            started_at=started,
            summary=summary,
        )

    # ------------------------------------------------------------------ reading
    def list_runs(
        self, who: Principal, *, connector: str | None = None, limit: int = 50, after: int = 0
    ) -> list[RunRecord]:
        who.require(Permission.CONNECTORS_READ)
        if connector is not None and connector not in self._runners:
            raise ValidationFailure("unknown connector")
        limit = max(1, min(limit, _MAX_PAGE))
        rows = self._db.query(
            "SELECT * FROM connector_runs WHERE tenant_id = ? AND run_id > ?"
            " AND (? IS NULL OR connector = ?) ORDER BY run_id LIMIT ?",
            (who.tenant_id, after, connector, connector, limit),
        )
        return [self._run_of(r) for r in rows]

    @staticmethod
    def _run_of(r: Any) -> RunRecord:
        return RunRecord(
            run_id=r["run_id"],
            connector=r["connector"],
            status=r["status"],
            started_by=r["started_by"],
            started_at=r["started_at"],
            summary=json.loads(r["summary_json"]),
        )

    def list_alerts(
        self, who: Principal, *, status: str | None = None, limit: int = 50, after: int = 0
    ) -> list[AlertRecord]:
        who.require(Permission.CONNECTORS_READ)
        if status not in (None, "open", "acknowledged"):
            raise ValidationFailure("status must be open or acknowledged")
        limit = max(1, min(limit, _MAX_PAGE))
        rows = self._db.query(
            "SELECT * FROM source_alerts WHERE tenant_id = ? AND alert_id > ?"
            " AND (? IS NULL OR status = ?) ORDER BY alert_id LIMIT ?",
            (who.tenant_id, after, status, status, limit),
        )
        return [AlertRecord(**{k: r[k] for k in AlertRecord.model_fields}) for r in rows]

    def acknowledge(self, who: Principal, alert_id: int) -> AlertRecord:
        who.require(Permission.ALERTS_ACK)
        with self._db.transaction():
            changed = self._db.execute(
                "UPDATE source_alerts SET status = 'acknowledged', acknowledged_by = ?,"
                " acknowledged_at = ? WHERE tenant_id = ? AND alert_id = ? AND status = 'open'",
                (f"user:{who.user_id}", self._clock.now().isoformat(), who.tenant_id, alert_id),
            )
            row = self._db.query(
                "SELECT * FROM source_alerts WHERE tenant_id = ? AND alert_id = ?",
                (who.tenant_id, alert_id),
            )
            if not row:
                raise NotFoundError("alert not found")
            if not changed:
                raise ConflictError("alert is already acknowledged")
            self._audit.append(
                who.tenant_id,
                f"user:{who.user_id}",
                "alert.acknowledged",
                str(alert_id),
                {"source": row[0]["source"], "item_key": row[0]["item_key"]},
            )
        return AlertRecord(**{k: row[0][k] for k in AlertRecord.model_fields})
