"""Route table. Each handler: authorise -> validate -> call the domain -> serialise.

======  ======================================  ===================  =====================
Verb    Path                                    Permission           Purpose
======  ======================================  ===================  =====================
GET     /health                                 public               liveness
GET     /v1/locales                             public               supported languages
GET     /v1/jurisdictions                       public               EU-27 + CH/US/GB/SG
GET     /v1/currencies                          public               supported currencies
POST    /v1/convert                             fx:convert           currency conversion
PUT     /v1/fx-rates                            fx:write             record daily rates
POST    /v1/rules                               rules:propose        propose a rule version
POST    /v1/ingest                              rules:ingest         machine-found candidate
GET     /v1/rules                               rules:read           current approved rules
GET     /v1/rules/applicable                    rules:read           rules for an entity/date
GET     /v1/rules/{key}                         rules:read           version history
POST    /v1/rules/{key}/versions/{n}/approve    rules:approve        four-eyes approval
POST    /v1/rules/{key}/versions/{n}/reject     rules:approve        reject a draft
POST    /v1/rules/{key}/withdraw                rules:withdraw       logical delete
GET     /v1/review-tasks                        tasks:read           open review tasks
GET     /v1/audit                               audit:read           audit entries
GET     /v1/audit/verify                        audit:read           hash-chain check
PUT     /v1/entities/{lei}                      registry:write       new entity version
GET     /v1/entities                            registry:read        list current entities
GET     /v1/entities/{lei}                      registry:read        current version
GET     /v1/entities/{lei}/history              registry:read        all versions
POST    /v1/ownership                           registry:write       add an ownership link
POST    /v1/ownership/{id}/end                  registry:write       give a link its last day
GET     /v1/entities/{lei}/ownership            registry:read        owners and holdings on a day
GET     /v1/entities/{lei}/group                registry:read        controlled group on a day
GET     /v1/entities/{lei}/ultimate-parent      registry:read        top of the group on a day
GET     /v1/entities/{lei}/rules                registry:read        rules that apply to it
======  ======================================  ===================  =====================
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from reggraph import __version__
from reggraph.api import schemas
from reggraph.api.deps import Services, get_services, require
from reggraph.core.errors import ValidationFailure
from reggraph.currency import CURRENCIES, Money, format_money
from reggraph.i18n import SUPPORTED_LOCALES
from reggraph.jurisdictions import all_jurisdictions, get_jurisdiction
from reggraph.registry import OwnershipInput, applicable_rules
from reggraph.registry.lei import validate_lei
from reggraph.regulation.models import ConsolidationBasis, EntityType
from reggraph.security.rbac import Permission, Principal

router = APIRouter()


def _dump(obj: BaseModel) -> dict[str, Any]:
    result: dict[str, Any] = obj.model_dump(mode="json")
    return result


# ------------------------------------------------------------------ public reference data
@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/v1/locales")
def locales() -> list[dict[str, str]]:
    return [
        {
            "tag": loc.tag,
            "language": loc.language,
            "native_name": loc.native_name,
            "group": loc.group,
        }
        for loc in SUPPORTED_LOCALES
    ]


@router.get("/v1/jurisdictions")
def jurisdictions() -> list[dict[str, Any]]:
    return [
        {
            "code": j.code,
            "name": j.name,
            "group": j.group,
            "currency": j.currency,
            "euro_area": j.euro_area,
            "languages": list(j.languages),
        }
        for j in all_jurisdictions()
    ]


@router.get("/v1/currencies")
def currencies() -> list[dict[str, Any]]:
    return [
        {"code": c.code, "name": c.name, "minor_units": c.minor_units, "symbol": c.symbol}
        for c in CURRENCIES.values()
    ]


# ------------------------------------------------------------------ currency
@router.post("/v1/convert")
def convert(
    body: schemas.ConvertRequest,
    who: Principal = Depends(require(Permission.FX_CONVERT)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    money = Money.of(body.amount, body.from_currency.upper())
    result = services.converter_for(who.tenant_id).convert(money, body.to.upper(), body.on)
    return {
        "source": {"amount": str(result.source.amount), "currency": result.source.currency},
        "result": {"amount": str(result.result.amount), "currency": result.result.currency},
        "rate": str(result.rate),
        "rate_date": result.rate_date.isoformat(),
        "sources": list(result.sources),
        "display": format_money(result.result, "en"),
    }


@router.put("/v1/fx-rates")
def put_rates(
    body: schemas.FxRatesRequest,
    who: Principal = Depends(require(Permission.FX_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, int]:
    return {"stored": services.ratebook.put(who, body.as_of, body.source, body.rates)}


# ------------------------------------------------------------------ rules
@router.post("/v1/rules", status_code=201)
def propose(
    body: schemas.ProposeRequest,
    who: Principal = Depends(require(Permission.RULES_PROPOSE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.rules.propose(who, body.rule, body.change_summary))


@router.post("/v1/ingest", status_code=201)
def ingest(
    body: schemas.IngestRequest,
    who: Principal = Depends(require(Permission.RULES_INGEST)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    version, task = services.rules.ingest(who, body.rule, body.source_ref)
    return {"version": _dump(version), "task": _dump(task)}


@router.get("/v1/rules")
def list_rules(
    who: Principal = Depends(require(Permission.RULES_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(v) for v in services.rules.current_rules(who)]


@router.get("/v1/rules/applicable")
def applicable(
    jurisdiction: str = Query(max_length=8),
    entity_type: EntityType = Query(),
    basis: ConsolidationBasis = Query(),
    on: date | None = Query(default=None),
    who: Principal = Depends(require(Permission.RULES_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    when = on or services.clock.now().date()
    found = services.rules.applicable(who, jurisdiction, entity_type, basis, when)
    return [{"in_transition": a.in_transition, "version": _dump(a.version)} for a in found]


@router.get("/v1/rules/{key}")
def rule_history(
    key: str,
    who: Principal = Depends(require(Permission.RULES_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(v) for v in services.rules.history(who, key)]


@router.post("/v1/rules/{key}/versions/{version_no}/approve")
def approve(
    key: str,
    version_no: int,
    body: schemas.DecisionRequest,
    who: Principal = Depends(require(Permission.RULES_APPROVE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.rules.approve(who, key, version_no, body.note))


@router.post("/v1/rules/{key}/versions/{version_no}/reject")
def reject(
    key: str,
    version_no: int,
    body: schemas.DecisionRequest,
    who: Principal = Depends(require(Permission.RULES_APPROVE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.rules.reject(who, key, version_no, body.note))


@router.post("/v1/rules/{key}/withdraw")
def withdraw(
    key: str,
    body: schemas.DecisionRequest,
    who: Principal = Depends(require(Permission.RULES_WITHDRAW)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.rules.withdraw(who, key, body.note))


@router.get("/v1/review-tasks")
def review_tasks(
    who: Principal = Depends(require(Permission.TASKS_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(t) for t in services.rules.open_tasks(who)]


# ------------------------------------------------------------------ audit
@router.get("/v1/audit")
def audit_entries(
    limit: int = Query(default=100, ge=1, le=500),
    after: int = Query(default=0, ge=0),
    who: Principal = Depends(require(Permission.AUDIT_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [asdict(e) for e in services.audit.entries(who.tenant_id, limit, after)]


@router.get("/v1/audit/verify")
def audit_verify(
    who: Principal = Depends(require(Permission.AUDIT_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    result = services.audit.verify(who.tenant_id)
    head_n, head_hash = services.audit.head(who.tenant_id)
    return {
        "ok": result.ok,
        "entries": result.entries,
        "first_bad": result.first_bad,
        "head": {"n": head_n, "hash": head_hash},
    }


# ------------------------------------------------------------------ registry
@router.put("/v1/entities/{lei}")
def put_entity(
    lei: str,
    body: schemas.EntityUpsertRequest,
    who: Principal = Depends(require(Permission.REGISTRY_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    if validate_lei(lei) != body.entity.lei:
        raise ValidationFailure("LEI in the path and in the body differ")
    return _dump(services.registry.upsert(who, body.entity, body.change_summary))


@router.get("/v1/entities")
def list_entities(
    jurisdiction: str | None = Query(default=None, max_length=8),
    entity_type: EntityType | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(active|dissolved)$"),
    limit: int = Query(default=50, ge=1, le=200),
    after: str = Query(default="", max_length=20),
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    code = None
    if jurisdiction is not None:
        jur = get_jurisdiction(jurisdiction.upper())
        if jur is None:
            raise ValidationFailure("unknown jurisdiction")
        code = jur.code
    found = services.registry.list_entities(
        who,
        jurisdiction=code,
        entity_type=entity_type.value if entity_type else None,
        status=status,
        limit=limit,
        after=after.upper(),
    )
    return [_dump(v) for v in found]


@router.get("/v1/entities/{lei}")
def get_entity(
    lei: str,
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.registry.get(who, lei))


@router.get("/v1/entities/{lei}/history")
def entity_history(
    lei: str,
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(v) for v in services.registry.history(who, lei)]


@router.post("/v1/ownership", status_code=201)
def add_ownership(
    body: OwnershipInput,
    who: Principal = Depends(require(Permission.REGISTRY_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.registry.link(who, body))


@router.post("/v1/ownership/{link_id}/end")
def end_ownership(
    link_id: int,
    body: schemas.EndLinkRequest,
    who: Principal = Depends(require(Permission.REGISTRY_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.registry.end_link(who, link_id, body.effective_until))


@router.get("/v1/entities/{lei}/ownership")
def entity_ownership(
    lei: str,
    on: date | None = Query(default=None),
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> dict[str, list[dict[str, Any]]]:
    day = on or services.clock.now().date()
    found = services.registry.links_of(who, lei, day)
    return {side: [_dump(item) for item in items] for side, items in found.items()}


@router.get("/v1/entities/{lei}/group")
def entity_group(
    lei: str,
    on: date | None = Query(default=None),
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    view = services.registry.group(who, lei, day)
    return {
        "root": view.root,
        "as_of": day.isoformat(),
        "note": "indicative control perimeter (more than 50 percent or branch); "
        "confirm against the applicable consolidation rules",
        "members": [
            {
                "lei": m.lei,
                "depth": m.depth,
                "via": m.via,
                "direct_percent": str(m.direct_percent),
                "effective_percent": str(m.effective_percent),
            }
            for m in view.members
        ],
        "participations": [
            {"holder": p.holder, "held": p.held, "percent": str(p.percent)}
            for p in view.participations
        ],
    }


@router.get("/v1/entities/{lei}/ultimate-parent")
def entity_ultimate_parent(
    lei: str,
    on: date | None = Query(default=None),
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(services.registry.ultimate_parent(who, lei, day))


@router.get("/v1/entities/{lei}/rules")
def entity_rules(
    lei: str,
    on: date | None = Query(default=None),
    who: Principal = Depends(require(Permission.REGISTRY_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    day = on or services.clock.now().date()
    matches = applicable_rules(who, services.registry, services.rules, lei, day)
    return [
        {
            "in_transition": m.in_transition,
            "because_of": {"jurisdictions": m.jurisdictions, "bases": m.bases},
            "version": _dump(m.version),
        }
        for m in matches
    ]
