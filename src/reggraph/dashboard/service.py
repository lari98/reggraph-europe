"""Management dashboard read model (Phase 5): one call, role-shaped, tenant-scoped.

The dashboard never reveals more than the caller's other endpoints would. Each section is
built only when the caller holds that section's read permission; the others are listed under
``omitted`` so the page can say "not available for your role" instead of showing blanks.

=====  ==============================================================================
D1     Section = permission: registry, exposure, insurance, stress, rules, rates and
       connectors each need the same permission as their own API. Viewers get no
       exposure, insurance or stress data.
D2     Every query filters by the tenant of the verified token.
D3     A failing section (for example a missing exchange rate) reports an error code
       for that section only; the rest of the dashboard still loads.
D4     Money is formatted with the currency's own number of decimals, as text.
D5     Text from stored records is returned as data; the page renders it with
       ``textContent`` only (never as HTML).
=====  ==============================================================================
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Final

from reggraph.core.errors import RegGraphError
from reggraph.currency import get_currency
from reggraph.exposure import ExposureAnalysis
from reggraph.security.rbac import Permission, Principal, Role
from reggraph.storage import Db

TOP_COUNTERPARTIES: Final = 5
RECENT_RUNS: Final = 5

_VIEW_BY_ROLE: Final = (
    (Role.TENANT_ADMIN, "administration"),
    (Role.COMPLIANCE_OFFICER, "compliance"),
    (Role.ANALYST, "analysis"),
    (Role.VIEWER, "overview"),
)


def _money(value: Decimal, currency: str) -> str:
    cur = get_currency(currency)
    places = cur.minor_units if cur else 2
    return format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN), "f")


class DashboardService:
    def __init__(self, db: Db, analysis: ExposureAnalysis) -> None:
        self._db = db
        self._analysis = analysis

    def view_name(self, who: Principal) -> str:
        for role, name in _VIEW_BY_ROLE:
            if role in who.roles:
                return name
        return "overview"

    def build(self, who: Principal, as_of: date, currency: str) -> dict[str, Any]:
        who.require(Permission.DASHBOARD_READ)
        builders: tuple[tuple[str, Permission, Callable[[], dict[str, Any]]], ...] = (
            ("registry", Permission.REGISTRY_READ, lambda: self._registry(who, as_of)),
            ("exposure", Permission.EXPOSURE_READ, lambda: self._exposure(who, as_of, currency)),
            ("insurance", Permission.INSURANCE_READ, lambda: self._insurance(who)),
            ("stress", Permission.STRESS_READ, lambda: self._stress(who)),
            ("rules", Permission.RULES_READ, lambda: self._rules(who)),
            ("rates", Permission.FX_CONVERT, lambda: self._rates(who)),
            ("connectors", Permission.CONNECTORS_READ, lambda: self._connectors(who)),
        )
        sections: dict[str, Any] = {}
        omitted: list[str] = []
        for name, permission, build in builders:
            if not who.can(permission):
                omitted.append(name)
                continue
            try:
                sections[name] = build()
            except RegGraphError as exc:
                sections[name] = {"error": exc.code}
        return {
            "view": self.view_name(who),
            "as_of": as_of.isoformat(),
            "currency": currency,
            "sections": sections,
            "omitted": omitted,
        }

    # ------------------------------------------------------------------ sections
    def _registry(self, who: Principal, as_of: date) -> dict[str, Any]:
        t = who.tenant_id
        rows = self._db.query(
            "SELECT e.entity_type AS k, e.status AS s, e.jurisdiction AS j FROM entity_versions e"
            " JOIN (SELECT lei, MAX(version_no) AS v FROM entity_versions WHERE tenant_id = ?"
            " GROUP BY lei) m ON e.lei = m.lei AND e.version_no = m.v WHERE e.tenant_id = ?",
            (t, t),
        )
        by_type: dict[str, int] = defaultdict(int)
        by_jur: dict[str, int] = defaultdict(int)
        active = 0
        for r in rows:
            by_type[r["k"]] += 1
            by_jur[r["j"]] += 1
            active += r["s"] == "active"
        links = self._db.query(
            "SELECT COUNT(*) AS n FROM ownership_links WHERE tenant_id = ? AND effective_from <= ?"
            " AND (effective_until IS NULL OR effective_until >= ?)",
            (t, as_of.isoformat(), as_of.isoformat()),
        )
        return {
            "entities": len(rows),
            "active": active,
            "by_type": dict(sorted(by_type.items())),
            "by_jurisdiction": dict(sorted(by_jur.items())),
            "ownership_links": int(links[0]["n"]),
        }

    def _exposure(self, who: Principal, as_of: date, currency: str) -> dict[str, Any]:
        flows, lineage = self._analysis.flows(who, as_of, currency)
        total = sum((f.amount for f in flows), Decimal(0))
        by_cpty: dict[str, Decimal] = defaultdict(Decimal)
        for f in flows:
            by_cpty[f.counterparty] += f.amount
        top = sorted(by_cpty.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_COUNTERPARTIES]
        names = self._names(who, [lei for lei, _ in top])
        return {
            "positions": lineage.positions_used,
            "batches": len(lineage.batch_ids),
            "total": _money(total, currency),
            "top_counterparties": [
                {
                    "lei": lei,
                    "name": names.get(lei, ""),
                    "amount": _money(amount, currency),
                    "share_percent": _money(amount * 100 / total, "EUR") if total else "0.00",
                }
                for lei, amount in top
            ],
        }

    def _names(self, who: Principal, leis: list[str]) -> dict[str, str]:
        """Legal names of at most ``TOP_COUNTERPARTIES`` entities (one fixed query each)."""
        names: dict[str, str] = {}
        for lei in leis:
            rows = self._db.query(
                "SELECT legal_name FROM entity_versions WHERE tenant_id = ? AND lei = ?"
                " ORDER BY version_no DESC LIMIT 1",
                (who.tenant_id, lei),
            )
            if rows:
                names[lei] = rows[0]["legal_name"]
        return names

    def _insurance(self, who: Principal) -> dict[str, Any]:
        t = who.tenant_id
        rows = self._db.query(
            "SELECT c.line AS line, COUNT(*) AS n FROM insurance_contract_versions c JOIN ("
            " SELECT contract_ref, MAX(version_no) AS v FROM insurance_contract_versions"
            " WHERE tenant_id = ? GROUP BY contract_ref) m"
            " ON c.contract_ref = m.contract_ref AND c.version_no = m.v"
            " WHERE c.tenant_id = ? GROUP BY c.line ORDER BY c.line",
            (t, t),
        )
        by_line = {r["line"]: int(r["n"]) for r in rows}
        return {"contracts": sum(by_line.values()), "by_line": by_line}

    def _stress(self, who: Principal) -> dict[str, Any]:
        rows = self._db.query(
            "SELECT run_id, scenario_key, as_of, currency, created_at, result_json FROM stress_runs"
            " WHERE tenant_id = ? ORDER BY run_id DESC LIMIT ?",
            (who.tenant_id, RECENT_RUNS),
        )
        recent = []
        for r in rows:
            totals = json.loads(r["result_json"]).get("totals", {})
            recent.append(
                {
                    "run_id": int(r["run_id"]),
                    "scenario_key": r["scenario_key"],
                    "as_of": r["as_of"],
                    "currency": r["currency"],
                    "created_at": r["created_at"],
                    "gross_loss": totals.get("gross_loss"),
                    "net_loss": totals.get("net_loss"),
                    "breaches": totals.get("breaches"),
                }
            )
        total = self._db.query(
            "SELECT COUNT(*) AS n FROM stress_runs WHERE tenant_id = ?", (who.tenant_id,)
        )
        scenarios = self._db.query(
            "SELECT COUNT(DISTINCT scenario_key) AS n FROM stress_scenario_versions"
            " WHERE tenant_id = ?",
            (who.tenant_id,),
        )
        return {
            "runs": int(total[0]["n"]),
            "scenarios": int(scenarios[0]["n"]),
            "recent_runs": recent,
        }

    def _rules(self, who: Principal) -> dict[str, Any]:
        rows = self._db.query(
            "SELECT status, COUNT(*) AS n FROM rule_versions WHERE tenant_id = ?"
            " GROUP BY status ORDER BY status",
            (who.tenant_id,),
        )
        data: dict[str, Any] = {"versions_by_status": {r["status"]: int(r["n"]) for r in rows}}
        if who.can(Permission.TASKS_READ):
            n = self._db.query(
                "SELECT COUNT(*) AS n FROM review_tasks WHERE tenant_id = ? AND status = 'open'",
                (who.tenant_id,),
            )
            data["open_review_tasks"] = int(n[0]["n"])
        return data

    def _rates(self, who: Principal) -> dict[str, Any]:
        rows = self._db.query(
            "SELECT MAX(as_of) AS d FROM fx_rates WHERE tenant_id = ?", (who.tenant_id,)
        )
        latest = rows[0]["d"]
        if latest is None:
            return {"latest_date": None, "currencies": 0}
        n = self._db.query(
            "SELECT COUNT(DISTINCT quote) AS n FROM fx_rates WHERE tenant_id = ?",
            (who.tenant_id,),
        )
        return {"latest_date": latest, "currencies": int(n[0]["n"])}

    def _connectors(self, who: Principal) -> dict[str, Any]:
        t = who.tenant_id
        last: dict[str, Any] = {}
        for name in ("ecb_rates", "eurlex_watch"):
            rows = self._db.query(
                "SELECT run_id, status, started_at FROM connector_runs WHERE tenant_id = ?"
                " AND connector = ? ORDER BY run_id DESC LIMIT 1",
                (t, name),
            )
            last[name] = (
                {
                    "run_id": int(rows[0]["run_id"]),
                    "status": rows[0]["status"],
                    "at": rows[0]["started_at"],
                }
                if rows
                else None
            )
        n = self._db.query(
            "SELECT COUNT(*) AS n FROM source_alerts WHERE tenant_id = ? AND status = 'open'", (t,)
        )
        return {"last_runs": last, "open_alerts": int(n[0]["n"])}
