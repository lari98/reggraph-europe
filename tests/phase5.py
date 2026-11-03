"""Helpers shared by the Phase 5 tests: fake network, ECB/EUR-Lex documents, SSO tokens."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient

from reggraph.api import create_app
from reggraph.config import Settings
from reggraph.core.clock import FixedClock
from reggraph.netio import FetchResult, UpstreamError
from tests.conftest import KEY, PG_DSN

ISSUER = "https://login.example-idp.test/realms/reg"
AUDIENCE = "reggraph-api"
JWKS_URL = "https://login.example-idp.test/realms/reg/keys"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FEED_URL = "https://eur-lex.europa.eu/feeds/new-acts.xml"


class FakeFetcher:
    """Answers from a dict; records every call. Values may be bytes or an exception."""

    def __init__(self, answers: dict[str, bytes | Exception] | None = None) -> None:
        self.answers = dict(answers or {})
        self.calls: list[str] = []

    def get(self, url: str, accept: Collection[str]) -> FetchResult:
        self.calls.append(url)
        answer = self.answers.get(url)
        if answer is None:
            raise UpstreamError("no such fake url")
        if isinstance(answer, Exception):
            raise answer
        return FetchResult(200, "application/xml", answer)


def ecb_xml(day: str = "2026-03-02", rates: dict[str, str] | None = None) -> bytes:
    rates = (
        rates
        if rates is not None
        else {"USD": "1.2500", "PLN": "4.2000", "ILS": "3.9", "SEK": "11.5"}
    )
    cubes = "".join(f'<Cube currency="{c}" rate="{r}"/>' for c, r in rates.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" '
        'xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">'
        f'<gesmes:subject>Reference rates</gesmes:subject><Cube><Cube time="{day}">{cubes}'
        "</Cube></Cube></gesmes:Envelope>"
    ).encode()


def rss(*items: tuple[str, str, str]) -> bytes:
    """(title, link, pubDate) triples -> RSS 2.0."""
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{d}</pubDate></item>"
        for t, link, d in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


def celex_link(celex: str) -> str:
    return f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{celex}"


def p5_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "token_keys": {"k1": KEY},
        "active_kid": "k1",
        "rate_burst": 500,
        "rate_per_minute": 5000,
        "eurlex_feed_url": FEED_URL,
    }
    if PG_DSN:
        base.update(database_url=PG_DSN, database_schema=f"t_{uuid.uuid4().hex[:16]}")
    base.update(over)
    return Settings(**base)


@contextmanager
def client_with(
    clock: FixedClock,
    *,
    connector_fetcher: FakeFetcher | None = None,
    oidc_fetcher: FakeFetcher | None = None,
    **settings_over: Any,
) -> Iterator[TestClient]:
    app = create_app(
        p5_settings(**settings_over),
        clock,
        connector_fetcher=connector_fetcher,
        oidc_fetcher=oidc_fetcher,
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        db = app.state.services.db
        if PG_DSN:
            db.drop_schema()
        db.close()


# ------------------------------------------------------------------ SSO helpers
class Idp:
    """A tiny pretend identity provider: one RSA key (and an optional EC key)."""

    def __init__(self, kid: str = "idp-1") -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec_private = ec.generate_private_key(ec.SECP256R1())

    def jwk(self, kid: str | None = None) -> dict[str, Any]:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        return {**public, "kid": kid or self.kid, "use": "sig", "alg": "RS256"}

    def jwks(self, *extra: dict[str, Any]) -> bytes:
        return json.dumps({"keys": [self.jwk(), *extra]}).encode()

    def claims(self, clock: FixedClock, **over: Any) -> dict[str, Any]:
        now = int(clock.now().timestamp())
        base: dict[str, Any] = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "alice@acme",
            "iat": now,
            "exp": now + 600,
            "reggraph_tenant": "acme",
            "reggraph_roles": ["analyst"],
        }
        base.update(over)
        return {k: v for k, v in base.items() if v is not None}

    def token(
        self,
        clock: FixedClock,
        *,
        key: Any = None,
        alg: str = "RS256",
        headers: dict[str, Any] | None = None,
        **over: Any,
    ) -> str:
        head = {"kid": self.kid, **(headers or {})}
        return jwt.encode(
            self.claims(clock, **over), key or self.private, algorithm=alg, headers=head
        )


def wait_ms(n: float) -> None:  # pragma: no cover - tiny helper for the timing test
    time.sleep(n / 1000)
