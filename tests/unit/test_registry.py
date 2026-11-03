"""Registry domain: LEI, models, group logic, store rules, rule mapping."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from reggraph.core.clock import FixedClock
from reggraph.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailure
from reggraph.registry import (
    EntityInput,
    EntityStore,
    OwnershipInput,
    applicable_rules,
    validate_lei,
)
from reggraph.registry import groups as g
from reggraph.regulation import RuleStore
from reggraph.regulation.models import RuleInput
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Principal, Role
from reggraph.storage import Database
from tests.conftest import entity_payload, make_lei, rule_payload

A, B, C, D = (make_lei(x) for x in ("AAAA", "BBBB", "CCCC", "DDDD"))
D0 = date(2025, 1, 1)


def _p(user: str = "ana", role: Role = Role.ANALYST, tenant: str = "acme") -> Principal:
    return Principal(user, tenant, frozenset({role}))


@pytest.fixture
def env() -> tuple[EntityStore, RuleStore, AuditLog]:
    clock = FixedClock(datetime(2026, 3, 2, 9, tzinfo=UTC))
    db = Database()
    audit = AuditLog(db, clock)
    return EntityStore(db, audit, clock), RuleStore(db, audit, clock), audit


def _ent(lei: str, **over: object) -> EntityInput:
    return EntityInput.model_validate(entity_payload(lei, **over))


def _own(parent: str, child: str, pct: str = "60", **over: object) -> OwnershipInput:
    body: dict[str, object] = {
        "parent_lei": parent,
        "child_lei": child,
        "percent": pct,
        "effective_from": "2024-01-01",
    }
    body.update(over)
    return OwnershipInput.model_validate(body)


def _seed(store: EntityStore, *leis: str) -> None:
    for lei in leis:
        store.upsert(_p(), _ent(lei, legal_name=f"Entity {lei[:4]}"), "initial")


# ---------------------------------------------------------------- LEI
@pytest.mark.parametrize("lei", ["HWUPKR0MPOU8FGXBT394", "7LTWFZYICNSX8D621K86"])
def test_real_leis_validate(lei: str) -> None:
    assert validate_lei(lei) == lei
    assert validate_lei(lei.lower()) == lei


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "short",
        "HWUPKR0MPOU8FGXBT395",
        "HWUPKR0MPOU8FGXBT39",
        "HWUPKR0MPOU8FGXBT3944",
        " HWUPKR0MPOU8FGXBT394",
        "HWUPKR0MPOU8FGXBT3٩٤",
        "HWUPKR0MPOU8FGXBT3A4",
        "'; DROP TABLE x;--aaaaa",
    ],
)
def test_bad_leis_rejected(bad: str) -> None:
    with pytest.raises(ValidationFailure):
        validate_lei(bad)


def test_make_lei_helper_is_valid() -> None:
    assert validate_lei(A) == A


# ---------------------------------------------------------------- models
def test_entity_normalises_and_defaults_currency() -> None:
    assert _ent(A, jurisdiction="uk").jurisdiction == "GB"
    assert _ent(A, jurisdiction="PL").effective_currency == "PLN"  # EU member outside the euro
    assert _ent(A, jurisdiction="DE").effective_currency == "EUR"
    assert _ent(A, jurisdiction="CH").effective_currency == "CHF"
    assert _ent(A, jurisdiction="DE", reporting_currency="usd").effective_currency == "USD"


@pytest.mark.parametrize(
    "over",
    [
        {"jurisdiction": "EU"},
        {"jurisdiction": "ZZ"},
        {"reporting_currency": "XXX"},
        {"reporting_currency": "HRK"},  # legacy, cannot be a reporting currency
        {"legal_name": "a‮b"},
        {"unknown_field": 1},
        {"lei": "HWUPKR0MPOU8FGXBT395"},
    ],
)
def test_entity_rejects_bad_input(over: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        EntityInput.model_validate({**entity_payload(A), **over})


def test_licence_period_and_validity() -> None:
    lic = {
        "licence_type": "banking",
        "authority": "BaFin",
        "jurisdiction": "DE",
        "valid_from": "2020-01-01",
        "valid_until": "2025-12-31",
    }
    ent = _ent(A, licences=[lic])
    assert ent.licences[0].valid_on(date(2025, 12, 31))  # inclusive end
    assert not ent.licences[0].valid_on(date(2026, 1, 1))
    with pytest.raises(ValueError):
        _ent(A, licences=[{**lic, "valid_until": "2019-01-01"}])


@pytest.mark.parametrize(
    "over",
    [
        {"percent": "0"},
        {"percent": "100.01"},
        {"percent": "-1"},
        {"percent": "NaN"},
        {"percent": "12.345"},
        {"parent_lei": B},
        {"effective_until": "2023-01-01"},  # parent == child
        {"kind": "branch", "percent": "80"},
    ],
)
def test_ownership_rejects_bad_input(over: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _own(A, B, **over)


# ---------------------------------------------------------------- pure group logic
def _lk(
    i: int,
    p: str,
    c: str,
    pct: str,
    frm: str = "2024-01-01",
    until: str | None = None,
    kind: str = "subsidiary",
) -> g.Link:
    return g.Link(
        i,
        p,
        c,
        Decimal(pct),
        kind,
        date.fromisoformat(frm),
        date.fromisoformat(until) if until else None,
    )


def test_cycle_detection_respects_periods() -> None:
    links = [_lk(1, A, B, "60"), _lk(2, B, C, "60")]
    assert g.would_create_cycle(links, C, A, D0, None)
    # a later, non-overlapping period is not a cycle
    ended = [_lk(1, A, B, "60", until="2024-12-31"), _lk(2, B, C, "60", until="2024-12-31")]
    assert not g.would_create_cycle(ended, C, A, date(2026, 1, 1), None)


def test_concurrent_percent_is_per_day_not_total() -> None:
    old = [
        _lk(1, A, C, "60", "2020-01-01", "2021-12-31"),
        _lk(2, B, C, "60", "2022-01-01", "2023-12-31"),
    ]
    new = _lk(0, D, C, "30", "2020-01-01", "2023-12-31")
    assert g.max_concurrent_percent(old, new) == Decimal(90)
    assert g.max_concurrent_percent([_lk(3, A, C, "80")], _lk(0, B, C, "30")) == Decimal(110)


def test_controlled_group_effective_percent_and_participation() -> None:
    links = [
        _lk(1, A, B, "60"),
        _lk(2, B, C, "80"),
        _lk(3, A, D, "30"),
        _lk(4, A, make_lei("EEEE"), "100", kind="branch"),
    ]
    view = g.controlled_group(links, A, D0)
    by = {m.lei: m for m in view.members}
    assert by[B].effective_percent == Decimal("60.0000")
    assert by[C].effective_percent == Decimal("48.0000") and by[C].depth == 2
    assert make_lei("EEEE") in by and D not in by
    assert [(p.held, p.percent) for p in view.participations] == [(D, Decimal(30))]
    assert g.controlled_group(links, A, date(2023, 1, 1)).members == ()  # before any link


def test_ultimate_parent_and_exactly_50_is_not_control() -> None:
    links = [_lk(1, A, B, "60"), _lk(2, B, C, "51"), _lk(3, D, A, "50")]
    assert g.ultimate_parent(links, C, D0) == A  # 50 % is not control, climb stops at A
    assert g.ultimate_parent(links, A, D0) == A


def test_group_depth_is_capped() -> None:
    leis = [make_lei(f"N{i:03d}") for i in range(g.MAX_DEPTH + 5)]
    links = [_lk(i, leis[i], leis[i + 1], "100") for i in range(len(leis) - 1)]
    with pytest.raises(ValidationFailure):
        g.controlled_group(links, leis[0], D0)


# ---------------------------------------------------------------- store
def test_entity_versions_and_no_empty_change(env: tuple[EntityStore, RuleStore, AuditLog]) -> None:
    store, _, audit = env
    v1 = store.upsert(_p(), _ent(A), "initial")
    assert v1.version_no == 1
    with pytest.raises(ConflictError):
        store.upsert(_p(), _ent(A), "again")
    v2 = store.upsert(_p(), _ent(A, legal_name="Renamed Bank AG"), "rename")
    assert v2.version_no == 2 and [v.version_no for v in store.history(_p(), A)] == [1, 2]
    assert store.get(_p(), A).entity.legal_name == "Renamed Bank AG"
    assert audit.verify("acme").ok and audit.verify("acme").entries == 2


def test_dissolve_is_a_new_version_not_a_delete(
    env: tuple[EntityStore, RuleStore, AuditLog],
) -> None:
    store, _, _ = env
    _seed(store, A)
    store.upsert(_p(), _ent(A, legal_name="Entity AAAA", status="dissolved"), "wound up")
    assert store.get(_p(), A).entity.status.value == "dissolved"
    assert len(store.history(_p(), A)) == 2


def test_list_filters_and_cursor(env: tuple[EntityStore, RuleStore, AuditLog]) -> None:
    store, _, _ = env
    store.upsert(_p(), _ent(A, jurisdiction="DE"), "x")
    store.upsert(_p(), _ent(B, jurisdiction="PL", entity_type="insurer"), "x")
    store.upsert(_p(), _ent(C, jurisdiction="PL"), "x")
    assert {e.lei for e in store.list_entities(_p(), jurisdiction="PL")} == {B, C}
    assert [e.lei for e in store.list_entities(_p(), entity_type="insurer")] == [B]
    first = store.list_entities(_p(), limit=2)
    assert len(first) == 2
    assert [e.lei for e in store.list_entities(_p(), after=first[-1].lei)] == [
        x for x in sorted([A, B, C]) if x > first[-1].lei
    ]


def test_link_rules(env: tuple[EntityStore, RuleStore, AuditLog]) -> None:
    store, _, _ = env
    _seed(store, A, B, C)
    first = store.link(_p(), _own(A, B, "60"))
    assert first.link_id == 1
    with pytest.raises(NotFoundError):
        store.link(_p(), _own(A, make_lei("ZZZZ")))  # unknown entity
    with pytest.raises(ConflictError, match="overlapping"):
        store.link(_p(), _own(A, B, "10"))  # same pair, same period
    with pytest.raises(ConflictError, match="cycle"):
        store.link(_p(), _own(B, A, "10"))
    store.link(_p(), _own(C, B, "40"))  # 60 + 40 = 100 held in B
    _seed(store, D)
    with pytest.raises(ConflictError, match="100"):
        store.link(_p(), _own(D, B, "1"))


def test_end_link_once_and_group_changes_over_time(
    env: tuple[EntityStore, RuleStore, AuditLog],
) -> None:
    store, _, _ = env
    _seed(store, A, B)
    link = store.link(_p(), _own(A, B, "70"))
    assert [m.lei for m in store.group(_p(), A, date(2025, 6, 1)).members] == [B]
    with pytest.raises(ValidationFailure):
        store.end_link(_p(), link.link_id, date(2023, 1, 1))
    store.end_link(_p(), link.link_id, date(2025, 12, 31))
    assert store.group(_p(), A, date(2025, 12, 31)).members
    assert not store.group(_p(), A, date(2026, 1, 1)).members
    with pytest.raises(ConflictError):
        store.end_link(_p(), link.link_id, date(2026, 6, 1))
    assert store.ultimate_parent(_p(), B, date(2025, 6, 1)).lei == A
    assert store.ultimate_parent(_p(), B, date(2026, 6, 1)).lei == B


def test_permissions_and_tenant_isolation(env: tuple[EntityStore, RuleStore, AuditLog]) -> None:
    store, _, _ = env
    _seed(store, A, B)
    with pytest.raises(ForbiddenError):
        store.upsert(_p("v", Role.VIEWER), _ent(C), "x")
    with pytest.raises(ForbiddenError):
        store.get(_p("feed", Role.INGESTION_SERVICE), A)
    other = _p("eve", Role.TENANT_ADMIN, "globex")
    with pytest.raises(NotFoundError):
        store.get(other, A)
    assert store.list_entities(other) == []
    with pytest.raises(NotFoundError):
        store.link(other, _own(A, B))
    link = store.link(_p(), _own(A, B))
    with pytest.raises(NotFoundError):
        store.end_link(other, link.link_id, date(2025, 1, 1))  # guessing link ids gets nothing


def test_database_refuses_edits_to_history(env: tuple[EntityStore, RuleStore, AuditLog]) -> None:
    store, _, _ = env
    _seed(store, A, B)
    store.link(_p(), _own(A, B))
    db = store._db
    for sql in (
        "UPDATE entity_versions SET legal_name = 'x'",
        "DELETE FROM entity_versions",
        "UPDATE ownership_links SET percent = '99'",
        "DELETE FROM ownership_links",
    ):
        with pytest.raises(Exception, match=r"immutable|cannot be deleted"):
            db.execute(sql)
    store.end_link(_p(), 1, date(2025, 1, 1))
    with pytest.raises(Exception, match="already ended"):
        db.execute("UPDATE ownership_links SET effective_until = '2030-01-01'")


# ---------------------------------------------------------------- rule mapping
def _approved(rules: RuleStore, key: str, **over: object) -> None:
    rules.propose(_p("prop"), RuleInput.model_validate(rule_payload(key, **over)), "x")
    rules.approve(_p("omar", Role.COMPLIANCE_OFFICER), key, 1)


def test_mapping_uses_home_country_licences_and_consolidation(
    env: tuple[EntityStore, RuleStore, AuditLog],
) -> None:
    store, rules, _ = env
    _approved(rules, "EU.RULE.INDIV", consolidation="individual")
    _approved(rules, "EU.RULE.CONSOL", consolidation="consolidated")
    _approved(rules, "CH.RULE.BRANCH", jurisdictions=["CH"], consolidation="both")
    lic = {
        "licence_type": "banking",
        "authority": "FINMA",
        "jurisdiction": "CH",
        "valid_from": "2024-01-01",
        "valid_until": "2025-06-30",
    }
    store.upsert(_p(), _ent(A, reporting_basis="both", licences=[lic]), "x")
    store.upsert(_p(), _ent(B, legal_name="Sub"), "x")
    mapped = applicable_rules(_p(), store, rules, A, date(2025, 6, 1))
    assert {m.version.rule_key for m in mapped} == {
        "EU.RULE.INDIV",
        "CH.RULE.BRANCH",
    }  # no group yet
    store.link(_p(), _own(A, B, "70"))
    mapped = {
        m.version.rule_key: m for m in applicable_rules(_p(), store, rules, A, date(2025, 6, 1))
    }
    assert set(mapped) == {"EU.RULE.INDIV", "EU.RULE.CONSOL", "CH.RULE.BRANCH"}
    assert mapped["EU.RULE.CONSOL"].bases == ["consolidated"]
    assert mapped["CH.RULE.BRANCH"].jurisdictions == ["CH"]
    expired = applicable_rules(_p(), store, rules, A, date(2025, 7, 1))
    assert "CH.RULE.BRANCH" not in {m.version.rule_key for m in expired}  # licence ended


def test_mapping_requires_both_permissions(env: tuple[EntityStore, RuleStore, AuditLog]) -> None:
    store, rules, _ = env
    _seed(store, A)
    with pytest.raises(ForbiddenError):
        applicable_rules(_p("feed", Role.INGESTION_SERVICE), store, rules, A, D0)
