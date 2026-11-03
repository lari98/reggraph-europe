"""Secret-safe logging (module: core)."""

from __future__ import annotations

import logging

import pytest

from reggraph.core.logsafe import MASK, install_log_redaction, redact

TOKEN = "v1.k1.eyJ2IjoxLCJzdWIiOiJhIn0.c2lnbmF0dXJlLWJ5dGVz"
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2ln-nature_x"


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (f"Authorization: Bearer {TOKEN}", TOKEN),
        (f"header Bearer {JWT} seen", JWT),
        (f"got {TOKEN} from client", "c2lnbmF0dXJlLWJ5dGVz"),
        (f"jwt={JWT}", "c2ln-nature_x"),
        ("password=hunter2hunter2", "hunter2"),
        ("db dsn='postgresql://u:p@h/db'", "postgresql://u:p@h/db"),
        ("connect postgresql://admin:S3cr3t!@db.internal:5432/x", "S3cr3t"),
        ("api_key: abcdefghijklmnop", "abcdefghijklmnop"),
        ('{"client_secret": "zzz-top-secret"}', "zzz-top-secret"),
        ("TOKEN_KEYS=k1:abcdefabcdefabcdefabcdefabcdef12", "abcdefabcdef"),
    ],
)
def test_secrets_are_masked(raw: str, secret: str) -> None:
    out = redact(raw)
    assert secret not in out and MASK in out


def test_plain_text_is_unchanged() -> None:
    text = "run 7 stored 12 rates for 2026-03-02 (skipped 2)"
    assert redact(text) == text


def test_huge_input_is_cut_and_fast() -> None:
    out = redact("a" * 5_000_000 + " password=x")
    assert len(out) < 9000 and out.endswith("[truncated]")


def test_records_are_masked_at_creation(caplog: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    install_log_redaction()  # idempotent
    log = logging.getLogger("reggraph.test")
    with caplog.at_level(logging.DEBUG):
        log.info("login with %s and password=%s", TOKEN, "pa55word")
        try:
            raise RuntimeError(f"upstream said Bearer {JWT}")
        except RuntimeError:
            log.exception("failed")
    text = caplog.text
    assert "pa55word" not in text and "c2lnbmF0dXJl" not in text and "c2ln-nature_x" not in text
    assert MASK in text


def test_bad_format_string_does_not_break_logging(caplog: pytest.LogCaptureFixture) -> None:
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("reggraph.test").info("value %d", "not-a-number")
    assert "value" in caplog.text
