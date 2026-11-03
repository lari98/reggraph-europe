"""Helpers shared by the Phase 3 tests (exposure, insurance, connectivity)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from reggraph.security.rbac import Role
from tests.conftest import entity_payload, make_lei

Auth = Callable[..., dict[str, str]]

BANK, BANK2, CORP, CORP2, INS, INS2, REINS = (
    make_lei("BANKONE"),
    make_lei("BANKTWO"),
    make_lei("CORPONE"),
    make_lei("CORPTWO"),
    make_lei("INSURERONE"),
    make_lei("INSURERTWO"),
    make_lei("REINSURER"),
)
KINDS = {
    BANK: "bank",
    BANK2: "bank",
    CORP: "corporate",
    CORP2: "corporate",
    INS: "insurer",
    INS2: "insurer",
    REINS: "reinsurer",
}


def register_all(client: TestClient, h: dict[str, str]) -> None:
    for lei, kind in KINDS.items():
        resp = client.put(
            f"/v1/entities/{lei}",
            headers=h,
            json={
                "entity": entity_payload(lei, entity_type=kind, legal_name=f"{kind} {lei[:6]}"),
                "change_summary": "setup",
            },
        )
        assert resp.status_code == 200, resp.text


def set_rates(client: TestClient, auth: Auth, day: str = "2026-03-02") -> None:
    resp = client.put(
        "/v1/fx-rates",
        headers=auth("root", Role.TENANT_ADMIN),
        json={"as_of": day, "source": "test", "rates": {"USD": "1.25", "PLN": "4.00"}},
    )
    assert resp.status_code == 200, resp.text


def pos(ref: str, holder: str, cpty: str, amount: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "exposure_ref": ref,
        "holder_lei": holder,
        "counterparty_lei": cpty,
        "exposure_type": "loan",
        "currency": "EUR",
        "amount": amount,
        "as_of": "2026-02-27",
    }
    body.update(over)
    return body


def batch(*positions: dict[str, Any], source: str = "core-banking", desc: str = "daily") -> dict:
    return {"source_system": source, "description": desc, "positions": list(positions)}


def contract(ref: str, insurer: str, insured: str, amount: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contract_ref": ref,
        "insurer_lei": insurer,
        "insured_lei": insured,
        "line": "non_life",
        "currency": "EUR",
        "sum_insured": amount,
        "start_date": "2026-01-01",
    }
    body.update(over)
    return body


def put_contract(client: TestClient, h: dict[str, str], body: dict[str, Any], note: str = "new"):  # type: ignore[no-untyped-def]
    return client.put(
        f"/v1/contracts/{body['contract_ref']}",
        headers=h,
        json={"contract": body, "change_summary": note},
    )
