"""SSO access-token verification (module: security)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from reggraph.core.clock import FixedClock
from reggraph.core.errors import AuthenticationError
from reggraph.netio import UpstreamError
from reggraph.security.oidc import OidcVerifier
from reggraph.security.rbac import Role
from tests.phase5 import AUDIENCE, ISSUER, JWKS_URL, FakeFetcher, Idp


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 3, 2, 9, 0, tzinfo=UTC))


@pytest.fixture
def idp() -> Idp:
    return Idp()


def verifier(idp: Idp, clock: FixedClock, fetcher: FakeFetcher | None = None) -> OidcVerifier:
    fake = fetcher or FakeFetcher({JWKS_URL: idp.jwks()})
    return OidcVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks_url=JWKS_URL, fetcher=fake, clock=clock
    )


def b64(obj: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def test_valid_token_becomes_a_principal(idp: Idp, clock: FixedClock) -> None:
    who = verifier(idp, clock).verify(idp.token(clock))
    assert (who.user_id, who.tenant_id, who.roles) == ("alice@acme", "acme", {Role.ANALYST})


def test_ec_keys_work_too(idp: Idp, clock: FixedClock) -> None:
    public = jwt.algorithms.ECAlgorithm.to_jwk(idp.ec_private.public_key(), as_dict=True)
    keys = json.dumps({"keys": [{**public, "kid": "ec-1", "use": "sig"}]}).encode()
    v = verifier(idp, clock, FakeFetcher({JWKS_URL: keys}))
    token = idp.token(clock, key=idp.ec_private, alg="ES256", headers={"kid": "ec-1"})
    assert v.verify(token).tenant_id == "acme"


def test_keys_are_cached_and_refresh_is_rate_limited(idp: Idp, clock: FixedClock) -> None:
    fetcher = FakeFetcher({JWKS_URL: idp.jwks()})
    v = verifier(idp, clock, fetcher)
    for _ in range(5):
        v.verify(idp.token(clock))
    assert len(fetcher.calls) == 1
    stranger = idp.token(clock, headers={"kid": "unknown"})
    for _ in range(20):  # an attacker spraying unknown key ids
        with pytest.raises(AuthenticationError):
            v.verify(stranger)
    assert len(fetcher.calls) == 1  # no re-fetch inside the 60 s gap
    clock.advance(seconds=61)
    with pytest.raises(AuthenticationError):
        v.verify(idp.token(clock, headers={"kid": "unknown"}))
    assert len(fetcher.calls) == 2


def test_key_rotation_is_picked_up(idp: Idp, clock: FixedClock) -> None:
    fetcher = FakeFetcher({JWKS_URL: idp.jwks()})
    v = verifier(idp, clock, fetcher)
    v.verify(idp.token(clock))
    clock.advance(seconds=120)
    fetcher.answers[JWKS_URL] = json.dumps({"keys": [idp.jwk("idp-2")]}).encode()
    assert v.verify(idp.token(clock, headers={"kid": "idp-2"})).user_id == "alice@acme"


def test_provider_outage_keeps_the_cached_keys(idp: Idp, clock: FixedClock) -> None:
    fetcher = FakeFetcher({JWKS_URL: idp.jwks()})
    v = verifier(idp, clock, fetcher)
    v.verify(idp.token(clock))
    fetcher.answers[JWKS_URL] = UpstreamError("down")
    clock.advance(seconds=700)  # cache is stale, provider is down
    assert v.verify(idp.token(clock)).user_id == "alice@acme"


def test_no_keys_at_all_means_no_login(idp: Idp, clock: FixedClock) -> None:
    v = verifier(idp, clock, FakeFetcher({JWKS_URL: UpstreamError("down")}))
    with pytest.raises(AuthenticationError):
        v.verify(idp.token(clock))


@pytest.mark.parametrize(
    "over",
    [
        {"iss": "https://evil.example"},
        {"aud": "someone-else"},
        {"aud": None},
        {"iss": None},
        {"sub": None},
        {"exp": None},
        {"iat": None},
        {"exp": 1},  # long expired
        {"exp": 1_772_442_000 + 600, "iat": 1_772_442_000 + 3600},  # issued in the future
        {"nbf": 1_772_442_000 + 3600},
        {"exp": 1_772_442_000 + 20 * 3600, "iat": 1_772_442_000},  # lifetime over 12 h
        {"exp": "soon"},
        {"exp": True},
        {"reggraph_tenant": None},
        {"reggraph_tenant": 7},
        {"reggraph_tenant": "../etc"},
        {"reggraph_tenant": "x" * 80},
        {"reggraph_roles": None},
        {"reggraph_roles": []},
        {"reggraph_roles": "analyst"},  # must be a list
        {"reggraph_roles": ["analyst", 7]},
        {"reggraph_roles": ["root"]},  # unknown role
        {"reggraph_roles": ["viewer"] * 9},
        {"sub": "bad subject with spaces"},
    ],
)
def test_bad_claims_are_refused(idp: Idp, clock: FixedClock, over: dict[str, object]) -> None:
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(idp.token(clock, **over))


def test_expiry_uses_the_injected_clock(idp: Idp, clock: FixedClock) -> None:
    v = verifier(idp, clock)
    token = idp.token(clock)
    clock.advance(seconds=601)
    with pytest.raises(AuthenticationError):
        v.verify(token)


def test_hmac_with_the_public_key_as_secret_is_refused(idp: Idp, clock: FixedClock) -> None:
    """Classic algorithm-confusion attack: sign with HS256 using the public key as the secret."""
    pem = jwt.algorithms.RSAAlgorithm(jwt.algorithms.RSAAlgorithm.SHA256).from_jwk(
        json.dumps(idp.jwk())
    )
    secret = pem.public_bytes(  # type: ignore[union-attr]
        __import__("cryptography").hazmat.primitives.serialization.Encoding.PEM,
        __import__(
            "cryptography"
        ).hazmat.primitives.serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    forged = b64({"alg": "HS256", "kid": idp.kid, "typ": "JWT"}) + "." + b64(idp.claims(clock))
    import hashlib
    import hmac

    sig = base64.urlsafe_b64encode(hmac.new(secret, forged.encode(), hashlib.sha256).digest())
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(forged + "." + sig.rstrip(b"=").decode())


def test_alg_none_is_refused(idp: Idp, clock: FixedClock) -> None:
    unsigned = b64({"alg": "none", "kid": idp.kid}) + "." + b64(idp.claims(clock)) + "."
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(unsigned)


def test_token_signed_by_another_key_is_refused(idp: Idp, clock: FixedClock) -> None:
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(idp.token(clock, key=attacker))


def test_keys_embedded_in_the_token_are_ignored(idp: Idp, clock: FixedClock) -> None:
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    evil_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(attacker.public_key()))
    token = idp.token(
        clock,
        key=attacker,
        headers={"jwk": evil_jwk, "jku": "https://evil.example/k", "x5u": "https://evil.example/c"},
    )
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(token)


def test_tampered_payload_is_refused(idp: Idp, clock: FixedClock) -> None:
    head, _, sig = idp.token(clock).split(".")
    forged = b64(idp.claims(clock, reggraph_roles=["tenant_admin"]))
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(f"{head}.{forged}.{sig}")


@pytest.mark.parametrize(
    "token", ["", "a", "a.b", "a.b.c", "x" * 9000, "....", "eyJ.eyJ.eyJ", "\x00.\x00.\x00"]
)
def test_garbage_is_refused(idp: Idp, clock: FixedClock, token: str) -> None:
    with pytest.raises(AuthenticationError):
        verifier(idp, clock).verify(token)


def test_odd_key_sets_are_survived(idp: Idp, clock: FixedClock) -> None:
    ec_pub = jwt.algorithms.ECAlgorithm.to_jwk(idp.ec_private.public_key(), as_dict=True)
    junk = [
        "text",
        {"kty": "oct", "kid": "sym", "k": "c2VjcmV0"},  # symmetric key: ignored
        {"kty": "RSA", "kid": "enc", "use": "enc", "n": "AQAB", "e": "AQAB"},
        {"kty": "RSA", "kid": 5},
        {"kty": "RSA", "kid": "broken", "n": "!!", "e": "!!"},
        {**ec_pub, "kid": "ec-ok"},
        idp.jwk(),
    ]
    body = json.dumps({"keys": junk}).encode()
    v = verifier(idp, clock, FakeFetcher({JWKS_URL: body}))
    assert v.verify(idp.token(clock)).tenant_id == "acme"
    for bad in (b"[]", b"{}", b'{"keys": 3}', b"not json"):
        w = verifier(idp, clock, FakeFetcher({JWKS_URL: bad}))
        with pytest.raises(AuthenticationError):
            w.verify(idp.token(clock))
