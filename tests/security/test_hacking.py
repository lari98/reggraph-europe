"""Attacker-style tests. Each class maps to a threat in docs/SECURITY_AND_TESTING.md."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from reggraph.api.deps import Services
from reggraph.currency import Money
from reggraph.i18n import negotiate
from reggraph.security.rbac import Role
from reggraph.security.validation import clean_text
from tests.conftest import rule_payload

pytestmark = pytest.mark.security
Auth = Callable[..., dict[str, str]]


def _propose(client: TestClient, headers: dict[str, str], **over: object):  # type: ignore[no-untyped-def]
    return client.post(
        "/v1/rules", headers=headers, json={"rule": rule_payload(**over), "change_summary": "x"}
    )


class TestAuthentication:
    def test_no_token_is_401(self, app_client: TestClient) -> None:
        for path in ("/v1/rules", "/v1/review-tasks", "/v1/audit", "/v1/audit/verify"):
            assert app_client.get(path).status_code == 401

    @pytest.mark.parametrize(
        "header",
        [
            "Bearer",
            "Bearer ",
            "Basic abc",
            "bearer v1.k1.e30.AAAA",
            "Bearer " + "A" * 5000,
            "Bearer v1.k1..",
            "Bearer null",
            "Bearer {}",
        ],
    )
    def test_garbage_tokens_are_401_never_500(self, app_client: TestClient, header: str) -> None:
        assert app_client.get("/v1/rules", headers={"Authorization": header}).status_code == 401

    def test_tampered_payload_is_rejected(self, app_client: TestClient, token_for) -> None:  # type: ignore[no-untyped-def]
        token = token_for("ana", Role.ANALYST)
        version, kid, payload, sig = token.split(".")
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        for field, value in (("roles", ["tenant_admin"]), ("tenant_id", "other")):
            forged = dict(data)
            forged[field if field in forged else "r"] = value
            body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
            bad = ".".join([version, kid, body, sig])
            assert (
                app_client.get("/v1/audit", headers={"Authorization": f"Bearer {bad}"}).status_code
                == 401
            )

    def test_alg_none_style_and_unknown_kid(self, app_client: TestClient) -> None:
        for token in ("v1.none.e30.", "v1.zzz.e30.AAAA", "eyJhbGciOiJub25lIn0.e30."):
            assert (
                app_client.get(
                    "/v1/rules", headers={"Authorization": f"Bearer {token}"}
                ).status_code
                == 401
            )

    def test_expired_token(
        self,
        app_client: TestClient,
        services: Services,
        token_for,  # type: ignore[no-untyped-def]
        clock,
    ) -> None:
        token = token_for("ana", Role.ANALYST)
        clock.advance(hours=13)
        assert (
            app_client.get("/v1/rules", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )


class TestAuthorization:
    def test_role_matrix(self, app_client: TestClient, auth: Auth) -> None:
        assert _propose(app_client, auth("v", Role.VIEWER)).status_code == 403
        assert app_client.get("/v1/audit", headers=auth("a", Role.ANALYST)).status_code == 403
        assert (
            app_client.put(
                "/v1/fx-rates",
                headers=auth("a", Role.ANALYST),
                json={"as_of": "2026-03-01", "source": "x", "rates": {"USD": "1.1"}},
            ).status_code
            == 403
        )
        assert (
            app_client.post(
                "/v1/ingest",
                headers=auth("a", Role.ANALYST),
                json={"rule": rule_payload(), "source_ref": "x"},
            ).status_code
            == 403
        )

    def test_admin_cannot_approve_rules(self, app_client: TestClient, auth: Auth) -> None:
        _propose(app_client, auth("ana", Role.ANALYST))
        r = app_client.post(
            "/v1/rules/EU.CRR3.CET1/versions/1/approve",
            headers=auth("root", Role.TENANT_ADMIN),
            json={},
        )
        assert r.status_code == 403

    def test_ingestion_service_cannot_approve_its_own_candidate(
        self, app_client: TestClient, auth: Auth
    ) -> None:
        bot = auth("feed", Role.INGESTION_SERVICE)
        app_client.post(
            "/v1/ingest", headers=bot, json={"rule": rule_payload(), "source_ref": "feed/1"}
        )
        assert (
            app_client.post(
                "/v1/rules/EU.CRR3.CET1/versions/1/approve", headers=bot, json={}
            ).status_code
            == 403
        )

    def test_four_eyes_over_http(self, app_client: TestClient, services: Services) -> None:
        from reggraph.security.rbac import Principal

        both = Principal("sam", "acme", frozenset({Role.ANALYST, Role.COMPLIANCE_OFFICER}))
        h = {"Authorization": f"Bearer {services.signer.issue(both)}"}
        _propose(app_client, h)
        assert (
            app_client.post(
                "/v1/rules/EU.CRR3.CET1/versions/1/approve", headers=h, json={}
            ).status_code
            == 403
        )


class TestTenantIsolation:
    def test_other_tenant_cannot_read_or_approve(self, app_client: TestClient, auth: Auth) -> None:
        _propose(app_client, auth("ana", Role.ANALYST, "acme"))
        eve = auth("eve", Role.COMPLIANCE_OFFICER, "evil")
        assert app_client.get("/v1/rules/EU.CRR3.CET1", headers=eve).status_code == 404
        assert (
            app_client.post(
                "/v1/rules/EU.CRR3.CET1/versions/1/approve", headers=eve, json={}
            ).status_code
            == 404
        )
        assert app_client.get("/v1/rules", headers=eve).json() == []
        assert app_client.get("/v1/review-tasks", headers=eve).json() == []

    def test_tenant_in_body_or_query_is_ignored(self, app_client: TestClient, auth: Auth) -> None:
        r = app_client.post(
            "/v1/rules",
            headers=auth("ana", Role.ANALYST, "acme"),
            json={"rule": rule_payload(), "change_summary": "x", "tenant_id": "evil"},
        )
        assert r.status_code == 422  # unknown field refused (mass assignment)

    def test_audit_and_fx_are_per_tenant(self, app_client: TestClient, auth: Auth) -> None:
        app_client.put(
            "/v1/fx-rates",
            headers=auth("r", Role.TENANT_ADMIN, "acme"),
            json={"as_of": "2026-03-02", "source": "x", "rates": {"USD": "1.1"}},
        )
        other = auth("r2", Role.TENANT_ADMIN, "globex")
        assert app_client.get("/v1/audit", headers=other).json() == []
        res = app_client.post(
            "/v1/convert", headers=other, json={"amount": "1", "from": "EUR", "to": "USD"}
        )
        assert res.status_code == 422  # no rate for this tenant


class TestInjection:
    PAYLOADS = [
        "' OR '1'='1",
        "'; DROP TABLE rule_versions;--",
        '" OR 1=1 --',
        "EU.X' UNION SELECT * FROM audit_log--",
        "%00",
        "${7*7}",
        "{{7*7}}",
        "$(id)",
    ]

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_sql_injection_in_path_and_body(
        self, app_client: TestClient, auth: Auth, payload: str
    ) -> None:
        ana = auth("ana", Role.ANALYST)
        _propose(app_client, ana)
        assert app_client.get(f"/v1/rules/{payload}", headers=ana).status_code in (404, 422)
        r = app_client.post(
            "/v1/rules",
            headers=ana,
            json={
                "rule": rule_payload(key="EU.SAFE.ONE", title=payload),
                "change_summary": payload,
            },
        )
        assert r.status_code in (201, 422)
        assert len(app_client.get("/v1/rules/EU.CRR3.CET1", headers=ana).json()) == 1

    def test_tables_survive(self, services: Services) -> None:
        rows = services.db.query("SELECT name FROM sqlite_master WHERE type='table'")
        assert {"rule_versions", "audit_log", "review_tasks", "fx_rates"} <= {
            r["name"] for r in rows
        }

    def test_template_injection_is_inert(self, services: Services) -> None:
        text = services.translator.translate("status.ok", "en", name="${secret}", x="{0.__class__}")
        assert "__class__" not in text

    @pytest.mark.parametrize(
        "tag", ["../../etc/passwd", "en/../../x", "en\x00", "%2e%2e", "xx" * 50]
    )
    def test_locale_path_traversal(self, tag: str) -> None:
        assert negotiate(tag) == "en"


class TestInputHygiene:
    @pytest.mark.parametrize("bad", ["a‮b", "a⁦b", "a​b", "a\x00b", "ab", "a\x1bb"])
    def test_trojan_source_and_control_chars_rejected(
        self, app_client: TestClient, auth: Auth, bad: str
    ) -> None:
        r = _propose(app_client, auth("ana", Role.ANALYST), title=bad)
        assert r.status_code == 422

    @pytest.mark.parametrize("amount", ["1e999999", "9" * 5000, "NaN", "-Infinity", "1" + "0" * 30])
    def test_decimal_dos_and_overflow(
        self, app_client: TestClient, auth: Auth, amount: str
    ) -> None:
        r = app_client.post(
            "/v1/convert",
            headers=auth("v", Role.VIEWER),
            json={"amount": amount, "from": "EUR", "to": "USD"},
        )
        assert r.status_code == 422

    def test_oversized_body_is_413(self, app_client: TestClient, auth: Auth) -> None:
        big = {"rule": rule_payload(), "change_summary": "x" * 400_000}
        r = app_client.post("/v1/rules", headers=auth("ana", Role.ANALYST), json=big)
        assert r.status_code == 413

    def test_chunked_oversize_is_413(self, app_client: TestClient, auth: Auth) -> None:
        def chunks():  # type: ignore[no-untyped-def]
            for _ in range(100):
                yield b"x" * 8192

        r = app_client.post("/v1/rules", headers=auth("ana", Role.ANALYST), content=chunks())
        assert r.status_code == 413

    def test_validation_errors_do_not_echo_input(self, app_client: TestClient, auth: Auth) -> None:
        secret = "SUPERSECRETVALUE"
        r = _propose(app_client, auth("ana", Role.ANALYST), jurisdictions=[secret])
        assert r.status_code == 422 and secret not in r.text

    def test_unknown_and_wrong_types_rejected(self, app_client: TestClient, auth: Auth) -> None:
        ana = auth("ana", Role.ANALYST)
        for body in (
            {"rule": rule_payload(status="approved"), "change_summary": "x"},
            {"rule": rule_payload(), "change_summary": 5},
            {"rule": "str", "change_summary": "x"},
            [],
            "x",
        ):
            assert app_client.post("/v1/rules", headers=ana, json=body).status_code == 422


class TestResponses:
    def test_security_headers_on_success_and_error(self, app_client: TestClient) -> None:
        for resp in (
            app_client.get("/health"),
            app_client.get("/nope"),
            app_client.get("/v1/rules"),
        ):
            assert resp.headers["x-content-type-options"] == "nosniff"
            assert resp.headers["x-frame-options"] == "DENY"
            assert resp.headers["cache-control"] == "no-store"
            assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]

    def test_request_id_header_abuse(self, app_client: TestClient) -> None:
        r = app_client.get("/health", headers={"X-Request-ID": "a\r\nSet-Cookie: x=1"})
        assert "set-cookie" not in r.headers
        r2 = app_client.get("/health", headers={"X-Request-ID": "good-id-12345"})
        assert r2.headers["x-request-id"] == "good-id-12345"

    def test_unexpected_exception_is_generic_500(
        self,
        app_client: TestClient,
        services: Services,
        auth: Auth,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(*a: object, **k: object) -> None:
            raise RuntimeError("secret path /srv/db.sqlite password=hunter2")

        monkeypatch.setattr(services.rules, "current_rules", boom)
        r = app_client.get("/v1/rules", headers=auth("v", Role.VIEWER))
        assert r.status_code == 500
        assert "hunter2" not in r.text and "Traceback" not in r.text and "/srv" not in r.text


class TestRateLimit:
    def test_flood_gets_429(self, settings, clock) -> None:  # type: ignore[no-untyped-def]
        from dataclasses import replace

        from reggraph.api import create_app

        client = TestClient(create_app(replace(settings, rate_burst=5, rate_per_minute=60), clock))
        codes = [client.get("/v1/locales").status_code for _ in range(12)]
        assert codes[:5] == [200] * 5 and 429 in codes
        blocked = client.get("/v1/locales")
        assert blocked.headers["retry-after"] and blocked.headers["x-content-type-options"]
        assert client.get("/health").status_code == 200  # liveness is never limited


class TestAuditTamper:
    def test_edit_through_api_is_impossible_and_db_edit_is_detected(
        self, app_client: TestClient, services: Services, auth: Auth
    ) -> None:
        _propose(app_client, auth("ana", Role.ANALYST))
        admin = auth("root", Role.TENANT_ADMIN)
        assert app_client.get("/v1/audit/verify", headers=admin).json()["ok"]
        with pytest.raises(Exception, match="append-only|immutable|abort|not allowed"):
            services.db.execute("UPDATE audit_log SET actor = 'user:evil'")
        services.db.execute("DROP TRIGGER audit_no_update")
        services.db.execute("UPDATE audit_log SET subject = 'forged' WHERE n = 1")
        assert not app_client.get("/v1/audit/verify", headers=admin).json()["ok"]

    def test_approved_rule_content_is_immutable(
        self, app_client: TestClient, auth: Auth, services: Services
    ) -> None:
        _propose(app_client, auth("ana", Role.ANALYST))
        with pytest.raises(Exception, match="immutable|append-only|abort|not allowed"):
            services.db.execute("UPDATE rule_versions SET change_summary = 'rewritten'")
        with pytest.raises(Exception, match="immutable|append-only|abort|not allowed|delete"):
            services.db.execute("DELETE FROM rule_versions")


class TestProperties:
    @settings(max_examples=200, deadline=None)
    @given(st.text(max_size=300))
    def test_clean_text_never_returns_dangerous_characters(self, value: str) -> None:
        try:
            out = clean_text(value, max_len=500, allow_empty=True)
        except ValueError:
            return
        assert not any(ord(c) < 32 or c in "‮⁦​﻿" for c in out)

    @settings(max_examples=200, deadline=None)
    @given(st.text(max_size=200))
    def test_negotiate_always_returns_a_supported_tag(self, header: str) -> None:
        from reggraph.i18n import is_supported

        assert is_supported(negotiate(header))

    @settings(max_examples=200, deadline=None)
    @given(
        st.decimals(
            allow_nan=False,
            allow_infinity=False,
            min_value=Decimal("-1e14"),
            max_value=Decimal("1e14"),
        )
    )
    def test_money_add_sub_roundtrip(self, amount: Decimal) -> None:
        a, b = Money.of(amount, "EUR"), Money.of("12.34", "EUR")
        assert (a + b) - b == a
