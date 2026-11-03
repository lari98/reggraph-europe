"""Shared fixtures. Every test gets a fresh in-memory database and a frozen clock."""

from __future__ import annotations

import os
import sqlite3
import uuid
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

# Set REGGRAPH_TEST_PG_DSN to run the whole suite against PostgreSQL instead of SQLite.
PG_DSN = os.environ.get("REGGRAPH_TEST_PG_DSN", "")

if PG_DSN:
    import psycopg

    DB_ERROR: tuple[type[Exception], ...] = (psycopg.Error,)
    DB_INTEGRITY: tuple[type[Exception], ...] = (psycopg.IntegrityError,)
else:
    DB_ERROR = (sqlite3.DatabaseError,)
    DB_INTEGRITY = (sqlite3.IntegrityError,)


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
    extra: dict[str, Any] = {}
    if PG_DSN:
        extra = {"database_url": PG_DSN, "database_schema": f"t_{uuid.uuid4().hex[:16]}"}
    return Settings(
        env="test",
        token_keys={"k1": KEY},
        active_kid="k1",
        rate_burst=500,
        rate_per_minute=5000,
        **extra,
    )


@pytest.fixture
def app_client(settings: Settings, clock: FixedClock) -> Iterator[TestClient]:
    client = TestClient(create_app(settings, clock), raise_server_exceptions=False)
    yield client
    db = client.app.state.services.db  # type: ignore[attr-defined]
    if PG_DSN:
        db.drop_schema()
    db.close()


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


def make_lei(seed: str) -> str:
    """A checksum-valid LEI built from an 18-character body (letters/digits)."""
    body = (seed.upper() + "0" * 18)[:18]
    digits = "".join(str(int(ch, 36)) for ch in body + "00")
    return body + f"{98 - int(digits) % 97:02d}"


def entity_payload(lei: str, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "lei": lei,
        "legal_name": "Example Bank AG",
        "entity_type": "bank",
        "jurisdiction": "DE",
        "reporting_basis": "individual",
    }
    body.update(over)
    return body


def table_names(db: Any) -> set[str]:
    """Names of the tables in the current schema, on either back end."""
    if PG_DSN:
        rows = db.query(
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
    else:
        rows = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    return {r["name"] for r in rows}


def drop_trigger(db: Any, name: str, table: str) -> None:
    """What an attacker with database access would do first (test helper)."""
    db.execute(f"DROP TRIGGER {name} ON {table}" if PG_DSN else f"DROP TRIGGER {name}")
