"""Input validation for exposure positions, insurance contracts and permissions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from reggraph.core.errors import ForbiddenError, ValidationFailure
from reggraph.currency import parse_money_amount
from reggraph.exposure import BatchInput, PositionInput
from reggraph.insurance import ContractInput
from reggraph.security.rbac import Permission, Principal, Role
from tests.phase3 import BANK, BANK2, INS, INS2, REINS, contract, pos


def test_parse_money_amount_rules() -> None:
    assert parse_money_amount("10.555", "EUR") == Decimal("10.56")  # half-even to minor units
    assert parse_money_amount(5, "HUF") == Decimal("5.00")
    for bad in (1.5, True, None, "1e3", "-1", " 5", "", "5" * 41, "NaN", "1" * 16):
        with pytest.raises(ValidationFailure):
            parse_money_amount(bad, "EUR")
    assert parse_money_amount("-1", "EUR", allow_negative=True) == Decimal("-1.00")


@pytest.mark.parametrize(
    "over",
    [
        {"amount": "1e3"},
        {"amount": 1.5},
        {"amount": "-5"},
        {"amount": "abc"},
        {"currency": "XXX"},
        {"currency": "EU"},
        {"counterparty_lei": BANK},  # exposed to itself
        {"maturity_date": "2026-01-01"},  # before as_of
        {"exposure_type": "magic"},
        {"exposure_ref": "has space"},
        {"exposure_ref": "a" * 65},
        {"extra_field": 1},
        {"holder_lei": "5493001KJTIIGC8Y1R13"},  # bad checksum
    ],
)
def test_position_rejects_bad_input(over: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, ValidationFailure)):
        PositionInput(**{**pos("R1", BANK, BANK2, "100"), **over})


def test_position_normalises() -> None:
    p = PositionInput(**pos("R1", BANK, BANK2, "100.5", currency="eur"))
    assert p.currency == "EUR" and p.amount == Decimal("100.50")


def test_batch_rules() -> None:
    one = pos("R1", BANK, BANK2, "1")
    with pytest.raises(ValidationError):
        BatchInput(source_system="s", description="d", positions=())
    with pytest.raises(ValidationError):  # same ref and day twice
        BatchInput(source_system="s", description="d", positions=(one, dict(one, amount="2")))  # type: ignore[arg-type]
    ok = BatchInput(
        source_system="s",
        description="d",
        positions=(one, pos("R1", BANK, BANK2, "1", as_of="2026-02-26")),
    )  # type: ignore[arg-type]
    same = BatchInput(source_system="other", description="x", positions=ok.positions)
    assert ok.content_hash() == same.content_hash()  # the hash covers the positions only


@pytest.mark.parametrize(
    "over",
    [
        {"sum_insured": "0"},
        {"sum_insured": "-1"},
        {"sum_insured": "1e5"},
        {"insured_lei": INS},  # insures itself
        {"end_date": "2025-12-31"},
        {"reinsures_ref": "C-1", "line": "life"},
        {"reinsures_ref": "C-0", "line": "reinsurance"},  # itself
        {"line": "pirate"},
        {"surprise": True},
    ],
)
def test_contract_rejects_bad_input(over: dict[str, Any]) -> None:
    with pytest.raises((ValidationError, ValidationFailure)):
        ContractInput(**{**contract("C-0", INS, BANK, "1000"), **over})


def test_contract_active_window_is_inclusive() -> None:
    c = ContractInput(
        **contract("C-1", INS2, BANK, "10", start_date="2026-01-01", end_date="2026-01-31")
    )
    assert c.active_on(c.start_date) and c.active_on(c.end_date)  # type: ignore[arg-type]
    assert not c.active_on(c.start_date.replace(day=1, month=12, year=2025))


def test_reinsurance_contract_may_name_underlying() -> None:
    c = ContractInput(**contract("R-1", REINS, INS, "10", line="reinsurance", reinsures_ref="C-1"))
    assert c.reinsures_ref == "C-1"


def test_permission_matrix_for_phase3() -> None:
    def can(role: Role, perm: Permission) -> bool:
        return Principal("u", "t", frozenset({role})).can(perm)

    new = (
        Permission.EXPOSURE_READ,
        Permission.EXPOSURE_WRITE,
        Permission.INSURANCE_READ,
        Permission.INSURANCE_WRITE,
    )
    for perm in new:
        assert can(Role.ANALYST, perm) and can(Role.TENANT_ADMIN, perm)
        assert not can(Role.VIEWER, perm) and not can(Role.INGESTION_SERVICE, perm)
    with pytest.raises(ForbiddenError):
        Principal("u", "t", frozenset({Role.VIEWER})).require(Permission.EXPOSURE_READ)
