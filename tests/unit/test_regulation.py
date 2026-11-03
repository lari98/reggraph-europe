"""Rule versioning, applicability, four-eyes approval (module: regulation)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from reggraph.core.clock import FixedClock
from reggraph.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailure
from reggraph.regulation import RuleStore
from reggraph.regulation.models import ConsolidationBasis, EntityType, RuleInput, RuleStatus
from reggraph.regulation.versioning import diff, is_material
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Principal, Role
from reggraph.storage import Database
from tests.conftest import rule_payload


def _p(user: str, role: Role, tenant: str = "acme") -> Principal:
    return Principal(user, tenant, frozenset({role}))


@pytest.fixture
def store() -> tuple[RuleStore, AuditLog]:
    clock = FixedClock(datetime(2026, 3, 2, 9, tzinfo=UTC))
    db = Database()
    audit = AuditLog(db, clock)
    return RuleStore(db, audit, clock), audit


def _rule(**over: object) -> RuleInput:
    return RuleInput.model_validate(rule_payload(**over))


def test_propose_then_approve_by_a_different_person(store: tuple[RuleStore, AuditLog]) -> None:
    rules, audit = store
    ana, omar = _p("ana", Role.ANALYST), _p("omar", Role.COMPLIANCE_OFFICER)
    draft = rules.propose(ana, _rule(), "initial")
    assert draft.status is RuleStatus.DRAFT and draft.version_no == 1
    approved = rules.approve(omar, "EU.CRR3.CET1", 1)
    assert approved.status is RuleStatus.APPROVED
    assert audit.verify("acme").ok


def test_four_eyes_blocks_self_approval(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    both = Principal("sam", "acme", frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER}))
    rules.propose(both, _rule(), "initial")
    with pytest.raises(ForbiddenError):
        rules.approve(both, "EU.CRR3.CET1", 1)


def test_analyst_cannot_approve(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    rules.propose(_p("ana", Role.ANALYST), _rule(), "initial")
    with pytest.raises(ForbiddenError):
        rules.approve(_p("bob", Role.ANALYST), "EU.CRR3.CET1", 1)


def test_one_open_draft_per_rule(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    ana = _p("ana", Role.ANALYST)
    rules.propose(ana, _rule(), "first")
    with pytest.raises(ConflictError):
        rules.propose(ana, _rule(title="Changed"), "second")


def test_new_version_supersedes_old_and_history_is_kept(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    ana, omar = _p("ana", Role.ANALYST), _p("omar", Role.COMPLIANCE_OFFICER)
    rules.propose(ana, _rule(), "v1")
    rules.approve(omar, "EU.CRR3.CET1", 1)
    rules.propose(ana, _rule(title="Minimum CET1 ratio (amended)"), "v2")
    rules.approve(omar, "EU.CRR3.CET1", 2)
    statuses = [v.status for v in rules.history(ana, "EU.CRR3.CET1")]
    assert statuses == [RuleStatus.SUPERSEDED, RuleStatus.APPROVED]
    assert [v.rule_key for v in rules.current_rules(ana)] == ["EU.CRR3.CET1"]


def test_withdraw_is_logical_delete(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    ana, omar = _p("ana", Role.ANALYST), _p("omar", Role.COMPLIANCE_OFFICER)
    rules.propose(ana, _rule(), "v1")
    rules.approve(omar, "EU.CRR3.CET1", 1)
    rules.withdraw(omar, "EU.CRR3.CET1", "repealed")
    assert rules.current_rules(ana) == []
    assert rules.history(ana, "EU.CRR3.CET1")[0].status is RuleStatus.WITHDRAWN


def test_ingestion_only_creates_draft_and_task(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    bot = _p("feed", Role.INGESTION_SERVICE)
    version, task = rules.ingest(bot, _rule(), "eur-lex:feed/1")
    assert version.status is RuleStatus.DRAFT and version.source == "ingestion"
    assert task.status == "open"
    assert rules.current_rules(bot) == []
    with pytest.raises(ForbiddenError):
        rules.approve(bot, "EU.CRR3.CET1", 1)


def test_unknown_rule_is_not_found(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    with pytest.raises(NotFoundError):
        rules.history(_p("ana", Role.ANALYST), "EU.NOPE.X")


def test_tenants_are_isolated(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    rules.propose(_p("ana", Role.ANALYST, "acme"), _rule(), "v1")
    with pytest.raises(NotFoundError):
        rules.history(_p("eve", Role.ANALYST, "other"), "EU.CRR3.CET1")


def test_applicability_by_date_and_scope(store: tuple[RuleStore, AuditLog]) -> None:
    rules, _ = store
    ana, omar = _p("ana", Role.ANALYST), _p("omar", Role.COMPLIANCE_OFFICER)
    rules.propose(ana, _rule(jurisdictions=["DE", "CH"]), "v1")
    rules.approve(omar, "EU.CRR3.CET1", 1)
    args = (EntityType.BANK, ConsolidationBasis.CONSOLIDATED)
    assert rules.applicable(ana, "DE", *args, date(2025, 6, 1))
    assert not rules.applicable(ana, "DE", *args, date(2024, 12, 31))  # not yet applicable
    assert not rules.applicable(ana, "FR", *args, date(2025, 6, 1))  # out of scope
    assert rules.applicable(ana, "CH", *args, date(2025, 6, 1))
    assert not rules.applicable(
        ana, "DE", EntityType.INSURER, ConsolidationBasis.CONSOLIDATED, date(2025, 6, 1)
    )


def test_dates_must_be_ordered() -> None:
    with pytest.raises(ValueError):
        _rule(effective_date="2023-01-01")


def test_material_change_detection() -> None:
    a, b = _rule(), _rule(title="Other title")
    assert not is_material(diff(a, b))  # a title edit is not material
    c = _rule(
        numeric_requirements=[
            {"name": "CET1", "comparator": ">=", "value": "5.0", "unit": "percent"}
        ]
    )
    assert is_material(diff(a, c))


def test_http_source_url_is_rejected() -> None:
    with pytest.raises(ValueError):
        _rule(source_url="http://example.org/x")
    with pytest.raises(ValueError):
        _rule(source_url="https://user:pw@example.org/x")


def test_validation_failure_is_a_value_error() -> None:
    assert issubclass(ValidationFailure, ValueError)
