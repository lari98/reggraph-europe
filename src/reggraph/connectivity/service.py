"""Connectivity views: banking exposure, insurance contracts and ownership in one picture.

====  ======================================================================================
C1    Everything is tenant-scoped through the verified principal; reading needs *both*
      exposure:read and insurance:read (and registry data is used only for ownership edges).
C2    Same reproducibility rule as the exposure queries: a query is its parameters, results
      carry lineage (exposure batches, contract versions, exchange rates) and a ``result_hash``.
C3    Contracts count on a day when ``start_date <= day <= end_date`` (end inclusive).
C4    Graph walks are bounded (depth 4, 500 nodes) and deterministic (sorted expansion).
====  ======================================================================================
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from reggraph.core.errors import ValidationFailure
from reggraph.currency import Converter, Money
from reggraph.exposure import analytics
from reggraph.exposure.service import (
    ExposureAnalysis,
    FxUsed,
    Lineage,
    ShareView,
    active_currency,
    seal,
)
from reggraph.insurance import ContractStore, ContractVersion
from reggraph.registry.lei import validate_lei
from reggraph.registry.store import EntityStore
from reggraph.security.rbac import Permission, Principal

_ZERO = Decimal(0)
MAX_NODES = 500


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class ContractView(_Out):
    contract_ref: str
    version_no: int
    insurer_lei: str
    insured_lei: str
    line: str
    sum_insured: Decimal  # in the query currency
    reinsured_by: list[str]


class ContractLineage(_Out):
    contract_ref: str
    version_no: int
    content_hash: str


class InsuranceProfile(_Out):
    insured_lei: str
    currency: str
    by_group: bool
    total_cover: Decimal
    hhi: Decimal
    top1: Decimal
    shares: list[ShareView]
    contracts: list[ContractView]
    contract_lineage: list[ContractLineage]
    fx: list[FxUsed]
    result_hash: str = ""


class Interconnection(_Out):
    lei: str
    exposure_total: Decimal
    cover_total: Decimal


class InterconnectionResult(_Out):
    holder_lei: str
    currency: str
    items: list[Interconnection]
    lineage: Lineage
    contract_lineage: list[ContractLineage]
    result_hash: str = ""


class GraphEdge(_Out):
    kind: str  # "exposure" | "insurance" | "ownership"
    from_lei: str
    to_lei: str
    amount: Decimal | None = None  # exposure total / sum insured, query currency
    percent: Decimal | None = None  # ownership only


class EcosystemResult(_Out):
    root_lei: str
    currency: str
    depth: int
    truncated: bool
    nodes: dict[str, int]
    edges: list[GraphEdge]
    lineage: Lineage
    contract_lineage: list[ContractLineage]
    result_hash: str = ""


class Connectivity:
    def __init__(
        self,
        exposures: ExposureAnalysis,
        contracts: ContractStore,
        registry: EntityStore,
        converter_for: Callable[[str], Converter],
    ) -> None:
        self._exposures = exposures
        self._contracts = contracts
        self._registry = registry
        self._converter_for = converter_for

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _need(who: Principal) -> None:
        who.require(Permission.EXPOSURE_READ)
        who.require(Permission.INSURANCE_READ)

    def _active_contracts(
        self, who: Principal, on: date, currency: str
    ) -> tuple[list[tuple[ContractVersion, Decimal]], list[FxUsed]]:
        """Current contracts in force on ``on`` with sum insured converted to ``currency``."""
        converter = self._converter_for(who.tenant_id)
        used: dict[str, FxUsed] = {}
        rows: list[tuple[ContractVersion, Decimal]] = []
        for item in self._contracts.all_current(who):
            c = item.contract
            if not c.active_on(on):
                continue
            conv = converter.convert(Money(c.sum_insured, c.currency), currency, on)
            used.setdefault(
                c.currency, FxUsed(currency=c.currency, rate=conv.rate, rate_date=conv.rate_date)
            )
            rows.append((item, conv.result.amount))
        return rows, sorted(used.values(), key=lambda f: f.currency)

    @staticmethod
    def _lineage(rows: list[tuple[ContractVersion, Decimal]]) -> list[ContractLineage]:
        return [
            ContractLineage(
                contract_ref=v.contract_ref, version_no=v.version_no, content_hash=v.content_hash
            )
            for v, _ in sorted(rows, key=lambda r: r[0].contract_ref)
        ]

    # ------------------------------------------------------------------ queries
    def insurance_profile(
        self, who: Principal, insured: str, on: date, currency: str, *, by_group: bool = False
    ) -> InsuranceProfile:
        """Who covers ``insured`` on ``on``, how much, and how concentrated that cover is."""
        who.require(Permission.INSURANCE_READ)
        key, cur = validate_lei(insured), active_currency(currency)
        rows, fx = self._active_contracts(who, on, cur)
        mine = [
            (v, amt)
            for v, amt in rows
            if v.contract.insured_lei == key and v.contract.line.value != "reinsurance"
        ]
        reinsurers: dict[str, list[str]] = {}
        for v, _ in rows:
            ref = v.contract.reinsures_ref
            if ref:
                reinsurers.setdefault(ref, []).append(v.contract.insurer_lei)
        group_of: dict[str, str] = {}

        def key_of(lei: str) -> str:
            if not by_group:
                return lei
            if lei not in group_of:
                group_of[lei] = self._registry.ultimate_parent_of(who.tenant_id, lei, on)
            return group_of[lei]

        flows = [analytics.Flow(key, key_of(v.contract.insurer_lei), amt) for v, amt in mine]
        conc = analytics.concentration(flows, lambda f: f.counterparty)
        return seal(
            InsuranceProfile(
                insured_lei=key,
                currency=cur,
                by_group=by_group,
                total_cover=conc.total,
                hhi=conc.hhi,
                top1=conc.top1,
                shares=[
                    ShareView(key=s.key, total=s.total, share=s.share, count=s.count)
                    for s in conc.shares
                ],
                contracts=[
                    ContractView(
                        contract_ref=v.contract_ref,
                        version_no=v.version_no,
                        insurer_lei=v.contract.insurer_lei,
                        insured_lei=v.contract.insured_lei,
                        line=v.contract.line.value,
                        sum_insured=amt,
                        reinsured_by=sorted(reinsurers.get(v.contract_ref, [])),
                    )
                    for v, amt in sorted(mine, key=lambda r: r[0].contract_ref)
                ],
                contract_lineage=self._lineage(mine),
                fx=fx,
            )
        )

    def interconnection(
        self, who: Principal, holder: str, on: date, currency: str, *, max_batch: int | None = None
    ) -> InterconnectionResult:
        """Entities that are both a counterparty of ``holder`` and an insurer of ``holder``.

        These are the places where one failure hits twice: the loss on the exposure and the
        loss of the cover that was meant to absorb it.
        """
        self._need(who)
        key, cur = validate_lei(holder), active_currency(currency)
        flows, lineage = self._exposures.flows(who, on, cur, holder=key, max_batch=max_batch)
        rows, _ = self._active_contracts(who, on, cur)
        cover: dict[str, Decimal] = {}
        used: list[tuple[ContractVersion, Decimal]] = []
        for v, amt in rows:
            if v.contract.insured_lei == key:
                cover[v.contract.insurer_lei] = cover.get(v.contract.insurer_lei, _ZERO) + amt
                used.append((v, amt))
        exposure: dict[str, Decimal] = {}
        for f in flows:
            exposure[f.counterparty] = exposure.get(f.counterparty, _ZERO) + f.amount
        both = sorted(set(cover) & set(exposure))
        items = [
            Interconnection(lei=lei, exposure_total=exposure[lei], cover_total=cover[lei])
            for lei in both
        ]
        items.sort(key=lambda i: (-(i.exposure_total + i.cover_total), i.lei))
        return seal(
            InterconnectionResult(
                holder_lei=key,
                currency=cur,
                items=items,
                lineage=lineage,
                contract_lineage=self._lineage(used),
            )
        )

    def ecosystem(
        self,
        who: Principal,
        root: str,
        on: date,
        currency: str,
        *,
        depth: int = 2,
        max_batch: int | None = None,
    ) -> EcosystemResult:
        """Everything within ``depth`` steps of ``root``: exposure, insurance, ownership edges."""
        self._need(who)
        who.require(Permission.REGISTRY_READ)
        key, cur = validate_lei(root), active_currency(currency)
        if not 0 <= depth <= analytics.MAX_DEPTH:
            raise ValidationFailure(f"depth must be between 0 and {analytics.MAX_DEPTH}")
        flows, lineage = self._exposures.flows(who, on, cur, max_batch=max_batch)
        rows, _ = self._active_contracts(who, on, cur)
        edges: list[GraphEdge] = [
            GraphEdge(kind="exposure", from_lei=e.holder, to_lei=e.counterparty, amount=e.total)
            for e in analytics.aggregate_edges(flows)
        ]
        by_pair: dict[tuple[str, str], Decimal] = {}
        for v, amt in rows:
            pair = (v.contract.insurer_lei, v.contract.insured_lei)
            by_pair[pair] = by_pair.get(pair, _ZERO) + amt
        edges += [
            GraphEdge(kind="insurance", from_lei=a, to_lei=b, amount=t)
            for (a, b), t in sorted(by_pair.items())
        ]
        edges += [
            GraphEdge(kind="ownership", from_lei=lk.parent, to_lei=lk.child, percent=lk.percent)
            for lk in sorted(self._registry.links_on(who.tenant_id, on), key=lambda x: x.link_id)
        ]
        adjacent: dict[str, set[str]] = {}
        for e in edges:
            adjacent.setdefault(e.from_lei, set()).add(e.to_lei)
            adjacent.setdefault(e.to_lei, set()).add(e.from_lei)
        seen: dict[str, int] = {key: 0}
        queue: deque[str] = deque([key])
        truncated = False
        while queue:
            node = queue.popleft()
            if seen[node] >= depth:
                continue
            for nxt in sorted(adjacent.get(node, ())):
                if nxt in seen:
                    continue
                if len(seen) >= MAX_NODES:
                    truncated = True
                    continue
                seen[nxt] = seen[node] + 1
                queue.append(nxt)
        inside = [e for e in edges if e.from_lei in seen and e.to_lei in seen]
        used = [
            (v, a)
            for v, a in rows
            if v.contract.insurer_lei in seen and v.contract.insured_lei in seen
        ]
        return seal(
            EcosystemResult(
                root_lei=key,
                currency=cur,
                depth=depth,
                truncated=truncated,
                nodes=dict(sorted(seen.items())),
                edges=inside,
                lineage=lineage,
                contract_lineage=self._lineage(used),
            )
        )
