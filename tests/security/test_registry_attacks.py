"""Attacker-style tests for the registry (Phase 2 exit gate: tenant isolation extended)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from reggraph.api.deps import Services
from reggraph.core.clock import FixedClock
from reggraph.core.errors import ConflictError, ValidationFailure
from reggraph.registry import EntityInput, EntityStore, OwnershipInput, validate_lei
from reggraph.registry import groups as g
from reggraph.security.audit import AuditLog
from reggraph.security.rbac import Principal, Role
from reggraph.storage import Database
from tests.conftest import entity_payload, make_lei

pytestmark = pytest.mark.security
Auth = Callable[..., dict[str, str]]
A, B, C = make_lei("AAAA"), make_lei("BBBB"), make_lei("CCCC")


def _put(c: TestClient, h: dict[str, str], lei: str, **over: object):  # type: ignore[no-untyped-def]
    body = {"entity": entity_payload(lei, **over), "change_summary": "x"}
    return c.put(f"/v1/entities/{lei}", headers=h, json=body)


def _own(c: TestClient, h: dict[str, str], p: str, ch: str, pct: str = "60", **over: object):  # type: ignore[no-untyped-def]
    body = {
        "parent_lei": p,
        "child_lei": ch,
        "percent": pct,
        "effective_from": "2024-01-01",
        **over,
    }
    return c.post("/v1/ownership", headers=h, json=body)


class TestRegistryAccess:
    ROUTES = [
        ("get", "/v1/entities"),
        ("get", f"/v1/entities/{A}"),
        ("get", f"/v1/entities/{A}/history"),
        ("get", f"/v1/entities/{A}/group"),
        ("get", f"/v1/entities/{A}/ownership"),
        ("get", f"/v1/entities/{A}/ultimate-parent"),
        ("get", f"/v1/entities/{A}/rules"),
        ("put", f"/v1/entities/{A}"),
        ("post", "/v1/ownership"),
        ("post", "/v1/ownership/1/end"),
    ]

    @pytest.mark.parametrize(("verb", "path"), ROUTES)
    def test_every_registry_route_needs_a_token(
        self, app_client: TestClient, verb: str, path: str
    ) -> None:
        assert getattr(app_client, verb)(path).status_code == 401

    def test_role_matrix(self, app_client: TestClient, auth: Auth) -> None:
        _put(app_client, auth("ana", Role.ANALYST), A)
        viewer, bot = auth("v", Role.VIEWER), auth("feed", Role.INGESTION_SERVICE)
        assert app_client.get(f"/v1/entities/{A}", headers=viewer).status_code == 200
        assert _put(app_client, viewer, B).status_code == 403
        assert _own(app_client, viewer, A, B).status_code == 403
        assert app_client.get(f"/v1/entities/{A}", headers=bot).status_code == 403
        assert app_client.get("/v1/entities", headers=bot).status_code == 403


class TestRegistryTenantIsolation:
    def test_other_tenant_sees_nothing(self, app_client: TestClient, auth: Auth) -> None:
        ana = auth("ana", Role.ANALYST, "acme")
        for lei in (A, B):
            _put(app_client, ana, lei)
        _own(app_client, ana, A, B)
        eve = auth("eve", Role.TENANT_ADMIN, "evil")
        for path in (
            f"/v1/entities/{A}",
            f"/v1/entities/{A}/history",
            f"/v1/entities/{A}/group",
            f"/v1/entities/{A}/ownership",
            f"/v1/entities/{B}/ultimate-parent",
            f"/v1/entities/{A}/rules",
        ):
            assert app_client.get(path, headers=eve).status_code == 404, path
        assert app_client.get("/v1/entities", headers=eve).json() == []

    def test_cannot_link_or_end_across_tenants(self, app_client: TestClient, auth: Auth) -> None:
        ana, eve = auth("ana", Role.ANALYST, "acme"), auth("eve", Role.ANALYST, "evil")
        for lei in (A, B):
            _put(app_client, ana, lei)
        assert _own(app_client, eve, A, B).status_code == 404  # cannot build on victim's entities
        _own(app_client, ana, A, B)
        ended = app_client.post(
            "/v1/ownership/1/end", headers=eve, json={"effective_until": "2025-01-01"}
        )
        assert ended.status_code == 404  # sequential link ids are guessable, access is not
        assert app_client.get(
            f"/v1/entities/{A}/group", params={"on": "2025-06-01"}, headers=ana
        ).json()["members"]

    def test_same_lei_can_exist_independently_in_two_tenants(
        self, app_client: TestClient, auth: Auth
    ) -> None:
        a1, a2 = auth("a", Role.ANALYST, "acme"), auth("b", Role.ANALYST, "globex")
        assert _put(app_client, a1, A, legal_name="Acme view").status_code == 200
        assert _put(app_client, a2, A, legal_name="Globex view").status_code == 200
        assert (
            app_client.get(f"/v1/entities/{A}", headers=a1).json()["entity"]["legal_name"]
            == "Acme view"
        )

    def test_audit_covers_registry_changes_and_detects_tampering(
        self, app_client: TestClient, services: Services, auth: Auth
    ) -> None:
        ana, admin = auth("ana", Role.ANALYST), auth("root", Role.TENANT_ADMIN)
        for lei in (A, B):
            _put(app_client, ana, lei)
        _own(app_client, ana, A, B)
        actions = [e["action"] for e in app_client.get("/v1/audit", headers=admin).json()]
        assert actions == ["entity.created", "entity.created", "ownership.created"]
        assert app_client.get("/v1/audit/verify", headers=admin).json()["ok"]
        services.db.execute("DROP TRIGGER audit_no_update")
        services.db.execute("UPDATE audit_log SET actor = 'user:evil' WHERE n = 2")
        assert not app_client.get("/v1/audit/verify", headers=admin).json()["ok"]


class TestRegistryInputAbuse:
    @pytest.mark.parametrize(
        "lei", ["' OR 1=1 --", "x" * 500, "%00", "../../etc", "HWUPKR0MPOU8FGXBT395"]
    )
    def test_hostile_lei_in_path_is_422_or_404_never_500(
        self, app_client: TestClient, auth: Auth, lei: str
    ) -> None:
        ana = auth("ana", Role.ANALYST)
        for path in (
            f"/v1/entities/{lei}",
            f"/v1/entities/{lei}/group",
            f"/v1/entities/{lei}/rules",
        ):
            assert app_client.get(path, headers=ana).status_code in (404, 422)

    @pytest.mark.parametrize(
        "params",
        [
            {"jurisdiction": "' OR 1=1"},
            {"entity_type": "bank; DROP"},
            {"status": "x"},
            {"limit": "0"},
            {"limit": "99999"},
            {"after": "x" * 100},
        ],
    )
    def test_hostile_list_filters(
        self, app_client: TestClient, auth: Auth, params: dict[str, str]
    ) -> None:
        r = app_client.get("/v1/entities", params=params, headers=auth("ana", Role.ANALYST))
        assert r.status_code == 422

    def test_mass_assignment_and_path_body_mismatch(
        self, app_client: TestClient, auth: Auth
    ) -> None:
        ana = auth("ana", Role.ANALYST)
        extra = {"entity": {**entity_payload(A), "tenant_id": "evil"}, "change_summary": "x"}
        assert app_client.put(f"/v1/entities/{A}", headers=ana, json=extra).status_code == 422
        top = {"entity": entity_payload(A), "change_summary": "x", "version_no": 99}
        assert app_client.put(f"/v1/entities/{A}", headers=ana, json=top).status_code == 422
        assert _put(app_client, ana, A).status_code == 200
        mismatch = {"entity": entity_payload(B), "change_summary": "x"}
        assert app_client.put(f"/v1/entities/{A}", headers=ana, json=mismatch).status_code == 422

    @pytest.mark.parametrize(
        "pct",
        ["100.01", "0", "-5", "NaN", "Infinity", "1e2", " 5", "5 ", "6.0e1", "12.345", "9" * 400],
    )
    def test_percentage_abuse(self, app_client: TestClient, auth: Auth, pct: str) -> None:
        ana = auth("ana", Role.ANALYST)
        for lei in (A, B):
            _put(app_client, ana, lei)
        assert _own(app_client, ana, A, B, pct).status_code == 422

    @pytest.mark.parametrize("bad", ["a‮b", "a​b", "a\x00b", "x" * 300])
    def test_hostile_names_rejected(self, app_client: TestClient, auth: Auth, bad: str) -> None:
        assert _put(app_client, auth("ana", Role.ANALYST), A, legal_name=bad).status_code == 422

    def test_injection_in_name_is_stored_inert(
        self, app_client: TestClient, services: Services, auth: Auth
    ) -> None:
        ana = auth("ana", Role.ANALYST)
        name = "Robert'); DROP TABLE entity_versions;--"
        assert _put(app_client, ana, A, legal_name=name).status_code == 200
        assert (
            app_client.get(f"/v1/entities/{A}", headers=ana).json()["entity"]["legal_name"] == name
        )
        assert services.db.query("SELECT count(*) AS n FROM entity_versions")[0]["n"] == 1


class TestGraphAbuse:
    def _store(self) -> tuple[EntityStore, Principal]:
        clock = FixedClock(datetime(2026, 3, 2, 9, tzinfo=UTC))
        db = Database()
        return EntityStore(db, AuditLog(db, clock), clock), Principal(
            "ana", "acme", frozenset({Role.ANALYST})
        )

    def test_long_chain_cannot_be_closed_into_a_cycle(self) -> None:
        store, who = self._store()
        leis = [make_lei(f"C{i:03d}") for i in range(30)]
        for lei in leis:
            store.upsert(who, EntityInput.model_validate(entity_payload(lei)), "x")
        for i in range(len(leis) - 1):
            store.link(
                who,
                OwnershipInput(
                    parent_lei=leis[i],
                    child_lei=leis[i + 1],
                    percent="100",
                    effective_from=date(2024, 1, 1),
                ),
            )
        with pytest.raises(Exception, match="cycle"):
            store.link(
                who,
                OwnershipInput(
                    parent_lei=leis[-1],
                    child_lei=leis[0],
                    percent="100",
                    effective_from=date(2024, 1, 1),
                ),
            )

    def test_deep_chain_query_is_refused_not_exhausted(self) -> None:
        store, who = self._store()
        leis = [make_lei(f"D{i:03d}") for i in range(g.MAX_DEPTH + 3)]
        for lei in leis:
            store.upsert(who, EntityInput.model_validate(entity_payload(lei)), "x")
        for i in range(len(leis) - 1):
            store.link(
                who,
                OwnershipInput(
                    parent_lei=leis[i],
                    child_lei=leis[i + 1],
                    percent="100",
                    effective_from=date(2024, 1, 1),
                ),
            )
        with pytest.raises(ValidationFailure):
            store.group(who, leis[0], date(2025, 1, 1))

    def test_over_100_percent_cannot_be_reached_by_many_small_links(self) -> None:
        store, who = self._store()
        target = make_lei("TGT")
        store.upsert(who, EntityInput.model_validate(entity_payload(target)), "x")
        accepted = 0
        refused = False
        for i in range(30):
            holder = make_lei(f"H{i:03d}")
            store.upsert(who, EntityInput.model_validate(entity_payload(holder)), "x")
            link = OwnershipInput(
                parent_lei=holder,
                child_lei=target,
                percent="9.99",
                effective_from=date(2024, 1, 1),
            )
            try:
                store.link(who, link)
            except ConflictError:
                refused = True
                break
            accepted += 1
        assert refused and accepted == 10  # the eleventh 9.99 % holder would pass 100 %


class TestRegistryProperties:
    @settings(max_examples=200, deadline=None)
    @given(st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=18))
    def test_generated_leis_validate_in_any_case(self, seed: str) -> None:
        lei = make_lei(seed)
        assert validate_lei(lei) == lei
        assert validate_lei(lei.lower()) == lei

    @settings(max_examples=200, deadline=None)
    @given(st.text(max_size=40))
    def test_validate_lei_never_crashes(self, text: str) -> None:
        try:
            out = validate_lei(text)
        except ValidationFailure:
            return
        assert len(out) == 20 and out == out.upper()
