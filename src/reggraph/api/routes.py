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
POST    /v1/exposures/batches                   exposure:write       load an exposure batch
GET     /v1/exposures/batches                   exposure:read        list batches (lineage)
GET     /v1/exposures/batches/{id}              exposure:read        one batch
GET     /v1/exposures/edges                     exposure:read        who is exposed to whom
GET     /v1/exposures/concentration/{lei}       exposure:read        concentration of a holder
GET     /v1/exposures/neighbourhood/{lei}       exposure:read        exposure graph around a node
PUT     /v1/contracts/{ref}                     insurance:write      new contract version
GET     /v1/contracts                           insurance:read       list current contracts
GET     /v1/contracts/{ref}                     insurance:read       current version
GET     /v1/contracts/{ref}/history             insurance:read       all versions
GET     /v1/entities/{lei}/insurance            insurance:read       cover of an entity
GET     /v1/entities/{lei}/interconnection      exposure+insurance   counterparty and insurer
GET     /v1/entities/{lei}/ecosystem            exposure+insurance   combined graph
PUT     /v1/stress/scenarios/{key}              stress:write         new scenario version
GET     /v1/stress/scenarios                    stress:read          list current scenarios
GET     /v1/stress/scenarios/{key}              stress:read          current or given version
GET     /v1/stress/scenarios/{key}/history      stress:read          all versions
POST    /v1/stress/runs                         stress:write+data    run, sign and store
GET     /v1/stress/runs                         stress:read          list runs
GET     /v1/stress/runs/{id}                    stress:read          run and result
GET     /v1/stress/runs/{id}/snapshot           stress:read          frozen inputs
POST    /v1/stress/runs/{id}/verify             stress:read          recompute and check
GET     /dashboard (+ app.js, app.css)          public page          the dashboard page (no data)
GET     /v1/dashboard                           dashboard:read       role-shaped overview
POST    /v1/connectors/{name}/run               connectors:run       run ecb_rates / eurlex_watch
GET     /v1/connectors/runs                     connectors:read      connector run history
GET     /v1/source-alerts                       connectors:read      EUR-Lex watch alerts
POST    /v1/source-alerts/{id}/acknowledge      alerts:ack           mark an alert as reviewed
======  ======================================  ===================  =====================
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel

from reggraph import __version__
from reggraph.api import schemas
from reggraph.api.deps import Services, get_services, require
from reggraph.core.errors import ValidationFailure
from reggraph.currency import CURRENCIES, Money, format_money
from reggraph.dashboard import STATIC_DIR
from reggraph.exposure import BatchInput
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
    body: schemas.EndLinkRequest,
    link_id: int = Path(ge=1, le=2_000_000_000),
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


# ------------------------------------------------------------------ exposure graph
_CCY = Query(default="EUR", pattern="^[A-Za-z]{3}$")
_MAX_BATCH = Query(default=None, ge=1, le=2_000_000_000)


@router.post("/v1/exposures/batches", status_code=201)
def load_batch(
    body: BatchInput,
    who: Principal = Depends(require(Permission.EXPOSURE_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.exposures.load_batch(who, body))


@router.get("/v1/exposures/batches")
def list_batches(
    limit: int = Query(default=50, ge=1, le=200),
    after: int = Query(default=0, ge=0, le=2_000_000_000),
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(b) for b in services.exposures.list_batches(who, limit=limit, after=after)]


@router.get("/v1/exposures/batches/{batch_id}")
def get_batch(
    batch_id: int = Path(ge=1, le=2_000_000_000),
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.exposures.get_batch(who, batch_id))


@router.get("/v1/exposures/edges")
def exposure_edges(
    on: date | None = Query(default=None),
    currency: str = _CCY,
    max_batch: int | None = _MAX_BATCH,
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(services.exposure_analysis.edges(who, day, currency, max_batch=max_batch))


@router.get("/v1/exposures/concentration/{lei}")
def exposure_concentration(
    lei: str,
    on: date | None = Query(default=None),
    currency: str = _CCY,
    by_group: bool = Query(default=False),
    max_batch: int | None = _MAX_BATCH,
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(
        services.exposure_analysis.concentration(
            who, lei, day, currency, by_group=by_group, max_batch=max_batch
        )
    )


@router.get("/v1/exposures/neighbourhood/{lei}")
def exposure_neighbourhood(
    lei: str,
    on: date | None = Query(default=None),
    currency: str = _CCY,
    depth: int = Query(default=2, ge=0, le=4),
    max_batch: int | None = _MAX_BATCH,
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(
        services.exposure_analysis.neighbourhood(
            who, lei, day, currency, depth=depth, max_batch=max_batch
        )
    )


# ------------------------------------------------------------------ insurance
@router.put("/v1/contracts/{ref}")
def put_contract(
    ref: str,
    body: schemas.ContractUpsertRequest,
    who: Principal = Depends(require(Permission.INSURANCE_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    if ref != body.contract.contract_ref:
        raise ValidationFailure("contract reference in the path and in the body differ")
    return _dump(services.contracts.upsert(who, body.contract, body.change_summary))


@router.get("/v1/contracts")
def list_contracts(
    insurer: str | None = Query(default=None, max_length=20),
    insured: str | None = Query(default=None, max_length=20),
    line: str | None = Query(
        default=None, pattern="^(life|non_life|health|credit_and_surety|reinsurance)$"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    after: str = Query(default="", max_length=64),
    who: Principal = Depends(require(Permission.INSURANCE_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    found = services.contracts.list_contracts(
        who, insurer=insurer, insured=insured, line=line, limit=limit, after=after
    )
    return [_dump(v) for v in found]


@router.get("/v1/contracts/{ref}")
def get_contract(
    ref: str,
    who: Principal = Depends(require(Permission.INSURANCE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.contracts.get(who, ref))


@router.get("/v1/contracts/{ref}/history")
def contract_history(
    ref: str,
    who: Principal = Depends(require(Permission.INSURANCE_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(v) for v in services.contracts.history(who, ref)]


@router.get("/v1/entities/{lei}/insurance")
def entity_insurance(
    lei: str,
    on: date | None = Query(default=None),
    currency: str = _CCY,
    by_group: bool = Query(default=False),
    who: Principal = Depends(require(Permission.INSURANCE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(
        services.connectivity.insurance_profile(who, lei, day, currency, by_group=by_group)
    )


@router.get("/v1/entities/{lei}/interconnection")
def entity_interconnection(
    lei: str,
    on: date | None = Query(default=None),
    currency: str = _CCY,
    max_batch: int | None = _MAX_BATCH,
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(
        services.connectivity.interconnection(who, lei, day, currency, max_batch=max_batch)
    )


@router.get("/v1/entities/{lei}/ecosystem")
def entity_ecosystem(
    lei: str,
    on: date | None = Query(default=None),
    currency: str = _CCY,
    depth: int = Query(default=2, ge=0, le=4),
    max_batch: int | None = _MAX_BATCH,
    who: Principal = Depends(require(Permission.EXPOSURE_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = on or services.clock.now().date()
    return _dump(
        services.connectivity.ecosystem(who, lei, day, currency, depth=depth, max_batch=max_batch)
    )


# ------------------------------------------------------------------ stress lab
_ID = Path(ge=1, le=2_000_000_000)


@router.put("/v1/stress/scenarios/{key}")
def put_scenario(
    key: str,
    body: schemas.ScenarioUpsertRequest,
    who: Principal = Depends(require(Permission.STRESS_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    if key != body.scenario.scenario_key:
        raise ValidationFailure("scenario key in the path and in the body differ")
    return _dump(services.stress_store.upsert_scenario(who, body.scenario, body.change_summary))


@router.get("/v1/stress/scenarios")
def list_scenarios(
    limit: int = Query(default=50, ge=1, le=200),
    after: str = Query(default="", max_length=64),
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(v) for v in services.stress_store.list_scenarios(who, limit=limit, after=after)]


@router.get("/v1/stress/scenarios/{key}")
def get_scenario(
    key: str,
    version: int | None = Query(default=None, ge=1, le=2_000_000_000),
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.stress_store.get_scenario(who, key, version))


@router.get("/v1/stress/scenarios/{key}/history")
def scenario_history(
    key: str,
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    return [_dump(v) for v in services.stress_store.scenario_history(who, key)]


@router.post("/v1/stress/runs", status_code=201)
def start_run(
    body: schemas.RunRequest,
    who: Principal = Depends(require(Permission.STRESS_WRITE)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    view = services.stress.run(
        who,
        body.scenario_key,
        version=body.scenario_version,
        on=body.as_of or services.clock.now().date(),
        currency=body.currency,
        buffers=body.capital_buffers,
        max_batch=body.max_batch,
    )
    return _dump(view)


@router.get("/v1/stress/runs")
def list_runs(
    scenario_key: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    after: int = Query(default=0, ge=0, le=2_000_000_000),
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    runs = services.stress_store.list_runs(who, scenario_key=scenario_key, limit=limit, after=after)
    return [_dump(r) for r in runs]


@router.get("/v1/stress/runs/{run_id}")
def get_run(
    run_id: int = _ID,
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.stress.get(who, run_id))


@router.get("/v1/stress/runs/{run_id}/snapshot")
def run_snapshot(
    run_id: int = _ID,
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return services.stress.snapshot_of(who, run_id)


@router.post("/v1/stress/runs/{run_id}/verify")
def verify_run(
    run_id: int = _ID,
    who: Principal = Depends(require(Permission.STRESS_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.stress.verify(who, run_id))


# ------------------------------------------------------------------ dashboard (Phase 5)
_PAGE_FILES = {
    "/dashboard": ("index.html", "text/html; charset=utf-8"),
    "/dashboard/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/dashboard/app.css": ("app.css", "text/css; charset=utf-8"),
}
_PAGES = {
    path: (STATIC_DIR.joinpath(name).read_bytes(), media)
    for path, (name, media) in _PAGE_FILES.items()
}  # read once at start-up: no path from the request ever touches the file system
_LABEL_KEYS = (
    "title",
    "token",
    "load",
    "registry",
    "exposure",
    "insurance",
    "stress",
    "rules",
    "rates",
    "connectors",
    "unavailable",
)


def _page(path: str) -> Response:
    body, media = _PAGES[path]
    return Response(content=body, media_type=media)


@router.get("/dashboard", include_in_schema=False)
def dashboard_page() -> Response:
    return _page("/dashboard")


@router.get("/dashboard/app.js", include_in_schema=False)
def dashboard_script() -> Response:
    return _page("/dashboard/app.js")


@router.get("/dashboard/app.css", include_in_schema=False)
def dashboard_style() -> Response:
    return _page("/dashboard/app.css")


@router.get("/v1/dashboard")
def dashboard(
    request: Request,
    as_of: date | None = Query(default=None),
    currency: str = Query(default="EUR", min_length=3, max_length=3),
    who: Principal = Depends(require(Permission.DASHBOARD_READ)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    day = as_of or services.clock.now().date()
    data = services.dashboard.build(who, day, currency.upper())
    locale = request.state.locale
    data["labels"] = {
        f"dashboard.{k}": services.translator.translate(f"dashboard.{k}", locale)
        for k in _LABEL_KEYS
    }
    return data


# ------------------------------------------------------------------ connectors (Phase 5)
@router.post("/v1/connectors/{name}/run")
def run_connector(
    name: str = Path(pattern=r"^[a-z_]{3,20}$"),
    who: Principal = Depends(require(Permission.CONNECTORS_RUN)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.connectors.run(who, name))


@router.get("/v1/connectors/runs")
def connector_runs(
    connector: str | None = Query(default=None, pattern=r"^[a-z_]{3,20}$"),
    limit: int = Query(default=50, ge=1, le=200),
    after: int = Query(default=0, ge=0, le=2_000_000_000),
    who: Principal = Depends(require(Permission.CONNECTORS_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    runs = services.connectors.list_runs(who, connector=connector, limit=limit, after=after)
    return [_dump(r) for r in runs]


@router.get("/v1/source-alerts")
def source_alerts(
    status: str | None = Query(default=None, pattern=r"^(open|acknowledged)$"),
    limit: int = Query(default=50, ge=1, le=200),
    after: int = Query(default=0, ge=0, le=2_000_000_000),
    who: Principal = Depends(require(Permission.CONNECTORS_READ)),
    services: Services = Depends(get_services),
) -> list[dict[str, Any]]:
    alerts = services.connectors.list_alerts(who, status=status, limit=limit, after=after)
    return [_dump(a) for a in alerts]


@router.post("/v1/source-alerts/{alert_id}/acknowledge")
def acknowledge_alert(
    alert_id: int = Path(ge=1, le=2_000_000_000),
    who: Principal = Depends(require(Permission.ALERTS_ACK)),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return _dump(services.connectors.acknowledge(who, alert_id))
