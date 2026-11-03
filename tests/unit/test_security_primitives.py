"""Tokens, rate limiter, validation and audit chain (module: security)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from reggraph.core.clock import FixedClock
from reggraph.core.errors import AuthenticationError, ValidationFailure
from reggraph.security.audit import AuditLog
from reggraph.security.ratelimit import RateLimiter
from reggraph.security.rbac import Principal, Role
from reggraph.security.tokens import TokenSigner
from reggraph.security.validation import clean_text, ensure_identifier, ensure_rule_key
from reggraph.storage import Database

KEY1, KEY2 = b"a" * 40, b"b" * 40


def _clock() -> FixedClock:
    return FixedClock(datetime(2026, 3, 2, 9, tzinfo=UTC))


def _who() -> Principal:
    return Principal("ana", "acme", frozenset({Role.ANALYST}))


def test_token_roundtrip_and_expiry() -> None:
    clock = _clock()
    signer = TokenSigner({"k1": KEY1}, "k1", clock)
    token = signer.issue(_who(), ttl_seconds=60)
    assert signer.verify(token) == _who()
    clock.advance(seconds=200)
    with pytest.raises(AuthenticationError):
        signer.verify(token)


def test_key_rotation_keeps_old_tokens_valid() -> None:
    clock = _clock()
    old = TokenSigner({"k1": KEY1}, "k1", clock).issue(_who())
    rotated = TokenSigner({"k1": KEY1, "k2": KEY2}, "k2", clock)
    assert rotated.verify(old) == _who()
    retired = TokenSigner({"k2": KEY2}, "k2", clock)
    with pytest.raises(AuthenticationError):
        retired.verify(old)


def test_weak_key_refused() -> None:
    with pytest.raises(ValidationFailure):
        TokenSigner({"k1": b"short"}, "k1", _clock())


def test_ttl_is_capped() -> None:
    signer = TokenSigner({"k1": KEY1}, "k1", _clock())
    with pytest.raises((ValidationFailure, AuthenticationError)):
        signer.issue(_who(), ttl_seconds=10**9)


def test_rate_limiter_blocks_then_recovers() -> None:
    clock = _clock()
    limiter = RateLimiter(clock, per_minute=60, burst=3)
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b")  # other clients unaffected
    clock.advance(seconds=2)
    assert limiter.allow("a")


def test_rate_limiter_key_table_is_bounded() -> None:
    limiter = RateLimiter(_clock(), per_minute=60, burst=3, max_keys=50)
    for i in range(500):
        limiter.allow(f"k{i}")
    assert limiter.size() <= 50


def test_clean_text_rules() -> None:
    assert clean_text("  hello  ") == "hello"
    assert clean_text("Café", max_len=10) == "Café"
    for bad in ("a\x00b", "a‮b", "a​b", "x" * 600, "", "line1\nline2"):
        with pytest.raises(ValidationFailure):
            clean_text(bad, max_len=500)
    assert clean_text("a\nb", multiline=True) == "a\nb"


def test_identifier_and_rule_key() -> None:
    assert ensure_identifier("acme-1", "tenant") == "acme-1"
    for bad in ("", "a b", "a;b", "../x", "x" * 100):
        with pytest.raises(ValidationFailure):
            ensure_identifier(bad, "tenant")
    assert ensure_rule_key("EU.CRR3.CET1") == "EU.CRR3.CET1"
    with pytest.raises(ValidationFailure):
        ensure_rule_key("eu crr3")


def test_audit_chain_detects_edit_and_truncation() -> None:
    clock = _clock()
    db = Database()
    audit = AuditLog(db, clock)
    for i in range(5):
        audit.append("acme", "user:ana", "x.y", f"s{i}", {"i": i})
    assert audit.verify("acme").ok
    n, head = audit.head("acme")
    assert n == 5
    db.execute("DROP TRIGGER audit_no_update")
    db.execute("UPDATE audit_log SET actor = 'user:evil' WHERE n = 3 AND tenant_id = 'acme'")
    result = audit.verify("acme")
    assert not result.ok and result.first_bad == 3


def test_audit_is_append_only_at_the_database() -> None:
    db = Database()
    audit = AuditLog(db, _clock())
    audit.append("acme", "user:ana", "x.y", "s", {})
    with pytest.raises(Exception, match="append-only|immutable|not allowed|abort"):
        db.execute("DELETE FROM audit_log")
    with pytest.raises(Exception, match="append-only|immutable|not allowed|abort"):
        db.execute("UPDATE audit_log SET actor = 'x'")
