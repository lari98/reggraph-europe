"""Shared fixtures. Every test gets a fresh in-memory database and a frozen clock."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from reggraph.api import create_app
from reggraph.api.deps import Services
from reggraph.config import Settings
from reggraph.core.clock import FixedClock
from reggraph.security.rbac import Principal, Role

KEY = b"unit-test-signing-key-0123456789abcdef"


def rule_payload(key: str = "EU.CRR3.CET1", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "rule_key": key,
        "title": "Minimum CET1 ratio",
        "source_language": "en",
        "citation": "Regulation (EU) 2024/1623, Art. 92",
        "source_url": "https://eur-lex.europa.eu/eli/reg/2024/1623/oj",
        "jurisdictions": ["EU"],
        "entity_types": ["bank"],
        "consolidation": "consolidated",
        "publication_date": "2024-06-19",
        "effective_date": "2024-07-09",
        "application_date": "2025-01-01",
        "numeric_requirements": [
            {"name": "CET1", "comparator": ">=", "value": "4.5", "unit": "percent"}
        ],
        "control_owner": "Head of Regulatory Reporting",
    }
    body.update(over)
    return body


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="test", token_keys={"k1": KEY}, active_kid="k1", rate_burst=500, rate_per_minute=5000
    )


@pytest.fixture
def app_client(settings: Settings, clock: FixedClock) -> Iterator[TestClient]:
    client = TestClient(create_app(settings, clock), raise_server_exceptions=False)
    yield client
    client.app.state.services.db.close()  # type: ignore[attr-defined]


@pytest.fixture
def services(app_client: TestClient) -> Services:
    svc: Services = app_client.app.state.services  # type: ignore[attr-defined]
    return svc


@pytest.fixture
def token_for(services: Services) -> Callable[..., str]:
    def make(user: str, role: Role, tenant: str = "acme") -> str:
        return services.signer.issue(Principal(user, tenant, frozenset({role})))

    return make


@pytest.fixture
def auth(token_for: Callable[..., str]) -> Callable[..., dict[str, str]]:
    def headers(user: str, role: Role, tenant: str = "acme") -> dict[str, str]:
        return {"Authorization": f"Bearer {token_for(user, role, tenant)}"}

    return headers
