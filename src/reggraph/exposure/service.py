"""Exposure queries: converted to one currency, with lineage and a reproducibility hash.

====  ======================================================================================
Q1    A query is fully described by (tenant, as-of date, currency, filters, max_batch). The same
      description over the same loaded data gives the same numbers and the same ``result_hash``.
Q2    Every result lists the batches it used (id and content hash) and each exchange rate
      applied (rate and the rate's own date): the data lineage.
Q3    A currency without a usable rate stops the query (RateUnavailableError); nothing is
      silently dropped or guessed.
Q4    Each position is converted and rounded on its own (to the currency's minor units), then
      summed, so totals do not depend on the order of the data.
====  ======================================================================================
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from reggraph.core.errors import ValidationFailure
from reggraph.currency import Converter, Money, get_currency
from reggraph.exposure import analytics
from reggraph.exposure.analytics import Flow
from reggraph.exposure.models import Position
from reggraph.exposure.store import ExposureStore
from reggraph.registry.lei import validate_lei
from reggraph.registry.store import EntityStore
from reggraph.security.rbac import Permission, Principal


class _Result(BaseModel):
    model_config = ConfigDict(frozen=True)


class FxUsed(_Result):
    currency: str
    rate: Decimal
    rate_date: date


class Lineage(_Result):
    as_of: date
    max_batch: int | None
    batch_ids: list[int]
    batch_hashes: list[str]
    positions_used: int
    fx: list[FxUsed]


class EdgeView(_Result):
    holder_lei: str
    counterparty_lei: str
    total: Decimal
    count: int


class ShareView(_Result):
    key: str
    total: Decimal
    share: Decimal
    count: int


class EdgesResult(_Result):
    currency: str
    edges: list[EdgeView]
    lineage: Lineage
    result_hash: str = ""


class ConcentrationResult(_Result):
    holder_lei: str
    currency: str
    by_group: bool
    total: Decimal
    hhi: Decimal
    top1: Decimal
    top3: Decimal
    shares: list[ShareView]
    lineage: Lineage
    result_hash: str = ""


class NeighbourhoodResult(_Result):
    root_lei: str
    currency: str
    depth: int
    truncated: bool
    nodes: dict[str, int]
    edges: list[EdgeView]
    lineage: Lineage
    result_hash: str = ""


R = TypeVar("R", bound=BaseModel)


def seal(result: R) -> R:
    """Attach the SHA-256 of the canonical JSON of everything except the hash itself."""
    body = json.dumps(
        result.model_dump(mode="json", exclude={"result_hash"}),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return result.model_copy(update={"result_hash": hashlib.sha256(body.encode()).hexdigest()})


def active_currency(code: str) -> str:
    cur = get_currency(code.strip().upper()) if isinstance(code, str) else None
    if cur is None or not cur.active:
        raise ValidationFailure("unsupported currency")
    return cur.code


class ExposureAnalysis:
    def __init__(
        self,
        store: ExposureStore,
        registry: EntityStore,
        converter_for: Callable[[str], Converter],
    ) -> None:
        self._store = store
        self._registry = registry
        self._converter_for = converter_for

    # ------------------------------------------------------------------ shared steps
    def flows(
        self,
        who: Principal,
        on: date,
        currency: str,
        *,
        holder: str | None = None,
        counterparty: str | None = None,
        max_batch: int | None = None,
    ) -> tuple[list[Flow], Lineage]:
        """Current positions converted to ``currency``, plus the lineage that explains them."""
        who.require(Permission.EXPOSURE_READ)
        target = active_currency(currency)
        positions: list[Position] = self._store.positions_as_of(
            who,
            on,
            holder=validate_lei(holder) if holder else None,
            counterparty=validate_lei(counterparty) if counterparty else None,
            max_batch=max_batch,
        )
        converter = self._converter_for(who.tenant_id)
        used: dict[str, FxUsed] = {}
        flows: list[Flow] = []
        for item in positions:
            pos = item.position
            conv = converter.convert(Money(pos.amount, pos.currency), target, on)
            used.setdefault(
                pos.currency,
                FxUsed(currency=pos.currency, rate=conv.rate, rate_date=conv.rate_date),
            )
            flows.append(Flow(pos.holder_lei, pos.counterparty_lei, conv.result.amount))
        batch_ids = sorted({p.batch_id for p in positions})
        hashes = [self._store.get_batch(who, b).content_hash for b in batch_ids]
        lineage = Lineage(
            as_of=on,
            max_batch=max_batch,
            batch_ids=batch_ids,
            batch_hashes=hashes,
            positions_used=len(positions),
            fx=sorted(used.values(), key=lambda f: f.currency),
        )
        return flows, lineage

    @staticmethod
    def _edge_views(edges: list[analytics.Edge]) -> list[EdgeView]:
        return [
            EdgeView(
                holder_lei=e.holder, counterparty_lei=e.counterparty, total=e.total, count=e.count
            )
            for e in edges
        ]

    # ------------------------------------------------------------------ queries
    def edges(
        self, who: Principal, on: date, currency: str, *, max_batch: int | None = None
    ) -> EdgesResult:
        flows, lineage = self.flows(who, on, currency, max_batch=max_batch)
        return seal(
            EdgesResult(
                currency=active_currency(currency),
                edges=self._edge_views(analytics.aggregate_edges(flows)),
                lineage=lineage,
            )
        )

    def concentration(
        self,
        who: Principal,
        holder: str,
        on: date,
        currency: str,
        *,
        by_group: bool = False,
        max_batch: int | None = None,
    ) -> ConcentrationResult:
        """Concentration of one holder's exposure, by counterparty or by its group."""
        key = validate_lei(holder)
        flows, lineage = self.flows(who, on, currency, holder=key, max_batch=max_batch)
        group_of: dict[str, str] = {}

        def key_of(flow: Flow) -> str:
            if not by_group:
                return flow.counterparty
            if flow.counterparty not in group_of:
                group_of[flow.counterparty] = self._registry.ultimate_parent_of(
                    who.tenant_id, flow.counterparty, on
                )
            return group_of[flow.counterparty]

        result = analytics.concentration(flows, key_of)
        return seal(
            ConcentrationResult(
                holder_lei=key,
                currency=active_currency(currency),
                by_group=by_group,
                total=result.total,
                hhi=result.hhi,
                top1=result.top1,
                top3=result.top3,
                shares=[
                    ShareView(key=s.key, total=s.total, share=s.share, count=s.count)
                    for s in result.shares
                ],
                lineage=lineage,
            )
        )

    def neighbourhood(
        self,
        who: Principal,
        root: str,
        on: date,
        currency: str,
        *,
        depth: int = 2,
        max_batch: int | None = None,
    ) -> NeighbourhoodResult:
        key = validate_lei(root)
        if not 0 <= depth <= analytics.MAX_DEPTH:
            raise ValidationFailure(f"depth must be between 0 and {analytics.MAX_DEPTH}")
        flows, lineage = self.flows(who, on, currency, max_batch=max_batch)
        hood = analytics.neighbourhood(analytics.aggregate_edges(flows), key, depth)
        return seal(
            NeighbourhoodResult(
                root_lei=key,
                currency=active_currency(currency),
                depth=depth,
                truncated=hood.truncated,
                nodes=dict(sorted(hood.depth_of.items())),
                edges=self._edge_views(list(hood.edges)),
                lineage=lineage,
            )
        )
