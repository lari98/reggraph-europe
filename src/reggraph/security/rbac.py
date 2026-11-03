"""Roles, permissions and the authenticated principal.

Permission matrix (``x`` = granted)::

    permission         viewer  analyst  compliance_officer  tenant_admin  ingestion_service
    -----------------  ------  -------  ------------------  ------------  -----------------
    ref:read             x        x             x                x               -
    fx:convert           x        x             x                x               -
    rules:read           x        x             x                x               x
    rules:propose        -        x             x                -               -
    rules:approve        -        -             x                -               -
    rules:withdraw       -        -             x                -               -
    rules:ingest         -        -             -                -               x
    tasks:read           -        x             x                x               -
    fx:write             -        -             -                x               -
    audit:read           -        -             -                x               -
    registry:read        x        x             x                x               -
    registry:write       -        x             x                x               -
    exposure:read        -        x             x                x               -
    exposure:write       -        x             x                x               -
    insurance:read       -        x             x                x               -
    insurance:write      -        x             x                x               -
    stress:read          -        x             x                x               -
    stress:write         -        x             x                x               -

Separation of duties: a tenant admin cannot approve rules, and nobody can approve a version
they proposed themselves (the four-eyes rule is enforced in ``RuleStore.approve``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from reggraph.core.errors import ForbiddenError
from reggraph.security.validation import ensure_identifier


class Role(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    COMPLIANCE_OFFICER = "compliance_officer"
    TENANT_ADMIN = "tenant_admin"
    INGESTION_SERVICE = "ingestion_service"


class Permission(StrEnum):
    REF_READ = "ref:read"
    FX_CONVERT = "fx:convert"
    RULES_READ = "rules:read"
    RULES_PROPOSE = "rules:propose"
    RULES_APPROVE = "rules:approve"
    RULES_WITHDRAW = "rules:withdraw"
    RULES_INGEST = "rules:ingest"
    TASKS_READ = "tasks:read"
    FX_WRITE = "fx:write"
    AUDIT_READ = "audit:read"
    REGISTRY_READ = "registry:read"
    REGISTRY_WRITE = "registry:write"
    EXPOSURE_READ = "exposure:read"
    EXPOSURE_WRITE = "exposure:write"
    INSURANCE_READ = "insurance:read"
    INSURANCE_WRITE = "insurance:write"
    STRESS_READ = "stress:read"
    STRESS_WRITE = "stress:write"


_P = Permission
_VIEWER = frozenset({_P.REF_READ, _P.FX_CONVERT, _P.RULES_READ, _P.REGISTRY_READ})
_DATA = frozenset(
    {
        _P.EXPOSURE_READ,
        _P.EXPOSURE_WRITE,
        _P.INSURANCE_READ,
        _P.INSURANCE_WRITE,
        _P.STRESS_READ,
        _P.STRESS_WRITE,
    }
)  # commercially sensitive exposure and insurance data: not for plain viewers
_ANALYST = _VIEWER | _DATA | {_P.RULES_PROPOSE, _P.TASKS_READ, _P.REGISTRY_WRITE}

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.ANALYST: _ANALYST,
    Role.COMPLIANCE_OFFICER: _ANALYST | {_P.RULES_APPROVE, _P.RULES_WITHDRAW},
    Role.TENANT_ADMIN: _VIEWER
    | _DATA
    | {_P.TASKS_READ, _P.FX_WRITE, _P.AUDIT_READ, _P.REGISTRY_WRITE},
    Role.INGESTION_SERVICE: frozenset({_P.RULES_INGEST, _P.RULES_READ}),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling. ``tenant_id`` comes from the verified token, never from request data."""

    user_id: str
    tenant_id: str
    roles: frozenset[Role]

    def __post_init__(self) -> None:
        ensure_identifier(self.user_id, "user id")
        ensure_identifier(self.tenant_id, "tenant id")

    def can(self, permission: Permission) -> bool:
        return any(permission in ROLE_PERMISSIONS[role] for role in self.roles)

    def require(self, permission: Permission) -> None:
        if not self.can(permission):
            raise ForbiddenError(f"missing permission {permission.value}")
