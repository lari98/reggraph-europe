"""Service container and request dependencies (authentication and permission checks)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request

from reggraph.config import Settings
from reggraph.connectivity import Connectivity
from reggraph.core.clock import Clock, SystemClock
from reggraph.core.errors import AuthenticationError
from reggraph.currency.rates import Converter
from reggraph.exposure import ExposureAnalysis, ExposureStore
from reggraph.fx import RateBook
from reggraph.i18n import Translator
from reggraph.insurance import ContractStore
from reggraph.registry import EntityStore
from reggraph.regulation import RuleStore
from reggraph.security.audit import AuditLog
from reggraph.security.ratelimit import RateLimiter
from reggraph.security.rbac import Permission, Principal
from reggraph.security.signing import ResultSigner
from reggraph.security.tokens import TokenSigner
from reggraph.storage import Db, open_database
from reggraph.stress import StressLab, StressStore


@dataclass
class Services:
    settings: Settings
    clock: Clock
    db: Db
    audit: AuditLog
    rules: RuleStore
    registry: EntityStore
    exposures: ExposureStore
    exposure_analysis: ExposureAnalysis
    contracts: ContractStore
    connectivity: Connectivity
    stress_store: StressStore
    stress: StressLab
    ratebook: RateBook
    translator: Translator
    signer: TokenSigner
    limiter: RateLimiter

    def converter_for(self, tenant_id: str) -> Converter:
        return Converter(self.ratebook.provider_for(tenant_id), self.clock)


def build_services(settings: Settings, clock: Clock | None = None) -> Services:
    clk = clock or SystemClock()
    db = open_database(settings.db_path, settings.database_url, settings.database_schema)
    audit = AuditLog(db, clk)
    ratebook = RateBook(db, audit, clk)
    registry = EntityStore(db, audit, clk)

    def converter_for(tenant_id: str) -> Converter:
        return Converter(ratebook.provider_for(tenant_id), clk)

    exposures = ExposureStore(db, audit, clk, registry)
    contracts = ContractStore(db, audit, clk, registry)
    analysis = ExposureAnalysis(exposures, registry, converter_for)
    stress_store = StressStore(db, audit, clk, registry)
    stress = StressLab(
        db,
        clk,
        stress_store,
        exposures,
        contracts,
        registry,
        ResultSigner(settings.token_keys, settings.active_kid),
        converter_for,
    )
    return Services(
        settings=settings,
        clock=clk,
        db=db,
        audit=audit,
        rules=RuleStore(db, audit, clk),
        registry=registry,
        exposures=exposures,
        exposure_analysis=analysis,
        contracts=contracts,
        connectivity=Connectivity(analysis, contracts, registry, converter_for),
        stress_store=stress_store,
        stress=stress,
        ratebook=ratebook,
        translator=Translator(),
        signer=TokenSigner(settings.token_keys, settings.active_kid, clk),
        limiter=RateLimiter(clk, per_minute=settings.rate_per_minute, burst=settings.rate_burst),
    )


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_locale(request: Request) -> str:
    locale: str = request.state.locale
    return locale


def get_principal(request: Request, services: Services = Depends(get_services)) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("missing bearer token")
    return services.signer.verify(token.strip())


def require(permission: Permission) -> Callable[[Principal], Principal]:
    """Dependency factory: the caller must be authenticated and hold ``permission``."""

    def checker(principal: Principal = Depends(get_principal)) -> Principal:
        principal.require(permission)
        return principal

    return checker
