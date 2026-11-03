"""OpenID Connect access-token verification (single sign-on) behind the same ``verify``
interface as :class:`reggraph.security.tokens.TokenSigner`.

RegGraph is a *resource server*: the identity provider (with MFA) signs a JWT access token,
and this class decides whether to trust it. Browser login (the redirect dance) belongs to the
front end / identity provider and is not implemented here.

=====  ===============================================================================
O1     Only asymmetric algorithms are accepted (RS256, ES256). ``none`` and HMAC
       algorithms are refused **before** any key is looked up (no algorithm confusion).
O2     The signing key is chosen only by ``kid`` from the provider's key set. Keys that
       arrive inside the token (``jwk``, ``jku``, ``x5u`` headers) are ignored.
O3     Issuer and audience must match exactly; ``exp``, ``iat`` and ``sub`` are required.
O4     Time checks use RegGraph's injectable clock with 60 s skew and a 12 h lifetime cap.
O5     The key set is fetched only through the sandboxed fetcher from the one configured
       host, cached for 10 minutes, and re-fetched for an unknown ``kid`` at most once
       a minute (an attacker cannot make us hammer the provider).
O6     Tenant and roles come from configured claims. Missing, mistyped, unknown or
       too many roles reject the token. Tenant ids must pass the usual identifier rule.
O7     Every failure is the same generic ``AuthenticationError``; reasons go to the log
       without any token content.
=====  ===============================================================================
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Final

import jwt
from jwt import PyJWK

from reggraph.core.clock import Clock
from reggraph.core.errors import AuthenticationError, ValidationFailure
from reggraph.netio import Fetcher, UpstreamError
from reggraph.security.rbac import Principal, Role

log = logging.getLogger("reggraph.security.oidc")

ALLOWED_ALGORITHMS: Final = ("RS256", "ES256")
_MAX_TOKEN: Final = 8192
_MAX_KEYS: Final = 50
_CACHE_SECONDS: Final = 600
_REFRESH_GAP_SECONDS: Final = 60
_SKEW: Final = 60
_MAX_TTL: Final = 12 * 3600
_JSON_TYPES: Final = ("application/json", "application/jwk-set+json")


class OidcVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        fetcher: Fetcher,
        clock: Clock,
        tenant_claim: str = "reggraph_tenant",
        roles_claim: str = "reggraph_roles",
    ) -> None:
        self._issuer, self._audience, self._jwks_url = issuer, audience, jwks_url
        self._fetcher, self._clock = fetcher, clock
        self._tenant_claim, self._roles_claim = tenant_claim, roles_claim
        self._keys: dict[str, PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def verify(self, token: str) -> Principal:
        try:
            return self._verify(token)
        except AuthenticationError:
            raise
        except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
            log.info("sso token rejected reason=%s", type(exc).__name__)
            raise AuthenticationError("sso token rejected") from exc

    # ------------------------------------------------------------------ internals
    def _verify(self, token: str) -> Principal:
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN:
            raise AuthenticationError("bad token size")
        header = jwt.get_unverified_header(token)
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGORITHMS:
            raise AuthenticationError("algorithm not allowed")
        if not isinstance(kid, str) or not 1 <= len(kid) <= 128:
            raise AuthenticationError("missing key id")
        key = self._key_for(kid)
        claims: dict[str, Any] = jwt.decode(
            token,
            key.key,
            algorithms=[alg],
            audience=self._audience,
            issuer=self._issuer,
            options={
                "require": ["exp", "iat", "sub", "iss", "aud"],
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
        self._check_times(claims)
        return self._principal(claims)

    def _check_times(self, claims: dict[str, Any]) -> None:
        now = int(self._clock.now().timestamp())
        values = {n: claims.get(n) for n in ("exp", "iat", "nbf")}
        for name, value in values.items():
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int | float)
            ):
                raise AuthenticationError(f"bad {name} claim")
        exp, iat, nbf = values["exp"], values["iat"], values["nbf"]
        if exp is None or iat is None:
            raise AuthenticationError("time claims missing")
        if exp <= now or iat > now + _SKEW or exp - iat > _MAX_TTL:
            raise AuthenticationError("token not valid now")
        if nbf is not None and nbf > now + _SKEW:
            raise AuthenticationError("token not valid yet")

    def _principal(self, claims: dict[str, Any]) -> Principal:
        sub, tenant, raw_roles = (
            claims.get("sub"),
            claims.get(self._tenant_claim),
            claims.get(self._roles_claim),
        )
        if not isinstance(sub, str) or not isinstance(tenant, str):
            raise AuthenticationError("identity claims missing")
        if not isinstance(raw_roles, list) or not 1 <= len(raw_roles) <= 8:
            raise AuthenticationError("role claim invalid")
        if not all(isinstance(r, str) for r in raw_roles):
            raise AuthenticationError("role claim invalid")
        try:
            roles = frozenset(Role(r) for r in raw_roles)
            return Principal(sub, tenant, roles)
        except (ValueError, ValidationFailure) as exc:
            raise AuthenticationError("identity claims invalid") from exc

    def _key_for(self, kid: str) -> PyJWK:
        now = self._clock.now().timestamp()
        with self._lock:
            stale = self._fetched_at is None or now - self._fetched_at > _CACHE_SECONDS
            if kid in self._keys and not stale:
                return self._keys[kid]
            can_refresh = self._fetched_at is None or now - self._fetched_at >= _REFRESH_GAP_SECONDS
            if can_refresh:
                self._refresh(now)
            key = self._keys.get(kid)
        if key is None:
            raise AuthenticationError("unknown signing key")
        return key

    def _refresh(self, now: float) -> None:
        self._fetched_at = now  # also on failure: a broken provider is not retried every request
        try:
            body = self._fetcher.get(self._jwks_url, _JSON_TYPES).body
            document = json.loads(body)
        except (UpstreamError, ValueError) as exc:
            log.warning("key set could not be loaded reason=%s", type(exc).__name__)
            return
        keys: dict[str, PyJWK] = {}
        entries = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(entries, list):
            log.warning("key set has an unexpected shape")
            return
        for entry in entries[:_MAX_KEYS]:
            if not isinstance(entry, dict) or entry.get("use") not in (None, "sig"):
                continue
            kid = entry.get("kid")
            if not isinstance(kid, str) or entry.get("kty") not in ("RSA", "EC"):
                continue
            try:
                keys[kid] = PyJWK(entry)
            except (jwt.PyJWTError, ValueError, TypeError):
                continue
        if keys:
            self._keys = keys
