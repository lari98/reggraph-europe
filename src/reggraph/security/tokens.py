"""Signed, expiring access tokens (HMAC-SHA256) with key rotation.

Format: ``v1.<kid>.<payload>.<signature>`` where payload is base64url(JSON).

Production note: in production the identity provider (SSO with MFA) issues tokens and this
verifier is replaced by an OIDC/JWKS verifier behind the same ``verify`` interface (Phase 4).
This implementation exists so Phase 1 is secure and testable on its own.

Defences
--------
===  ===============================================================================
T1   Fixed algorithm and version prefix: no "alg: none" or algorithm-confusion attack.
T2   Constant-time signature comparison (``hmac.compare_digest``).
T3   Keys must be at least 32 bytes; a key id selects the key, so keys can be rotated.
T4   Expiry, not-before (with 60 s skew) and a maximum lifetime are all enforced.
T5   Size limits before decoding (token 4 KiB, payload 2 KiB).
T6   Unknown roles or wrong types are rejected; every failure is the same generic error.
===  ===============================================================================
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from typing import Final

from reggraph.core.clock import Clock
from reggraph.core.errors import AuthenticationError, ValidationFailure
from reggraph.security.rbac import Principal, Role

MIN_KEY_BYTES: Final = 32
_KID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_MAX_TOKEN: Final = 4096
_MAX_PAYLOAD: Final = 2048


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.b64decode(text + pad, altchars=b"-_", validate=True)


class TokenSigner:
    def __init__(
        self,
        keys: Mapping[str, bytes],
        active_kid: str,
        clock: Clock,
        *,
        skew_seconds: int = 60,
        max_ttl_seconds: int = 12 * 3600,
    ) -> None:
        if active_kid not in keys:
            raise ValidationFailure("active key id missing")
        for kid, key in keys.items():
            if not _KID_RE.match(kid) or len(key) < MIN_KEY_BYTES:
                raise ValidationFailure("weak or malformed signing key")
        self._keys = dict(keys)
        self._active = active_kid
        self._clock = clock
        self._skew = skew_seconds
        self._max_ttl = max_ttl_seconds

    def _sign(self, kid: str, payload_b64: str) -> str:
        message = f"v1.{kid}.{payload_b64}".encode("ascii")
        return _b64e(hmac.new(self._keys[kid], message, hashlib.sha256).digest())

    def issue(self, principal: Principal, ttl_seconds: int = 900) -> str:
        if not 1 <= ttl_seconds <= self._max_ttl:
            raise ValidationFailure("ttl out of range")
        now = int(self._clock.now().timestamp())
        claims = {
            "v": 1,
            "sub": principal.user_id,
            "tid": principal.tenant_id,
            "roles": sorted(role.value for role in principal.roles),
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": secrets.token_hex(8),
        }
        payload = _b64e(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        return f"v1.{self._active}.{payload}.{self._sign(self._active, payload)}"

    def verify(self, token: str) -> Principal:
        """Return the principal, or raise ``AuthenticationError`` (always the same error)."""
        try:
            return self._verify(token)
        except AuthenticationError:
            raise
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise AuthenticationError("malformed token") from exc

    def _verify(self, token: str) -> Principal:
        if not isinstance(token, str) or len(token) > _MAX_TOKEN:
            raise AuthenticationError("bad token size")
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            raise AuthenticationError("bad token layout")
        _, kid, payload_b64, signature = parts
        if kid not in self._keys:
            raise AuthenticationError("unknown key id")
        expected = self._sign(kid, payload_b64)
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("ascii", "ignore")):
            raise AuthenticationError("bad signature")
        raw = _b64d(payload_b64)
        if len(raw) > _MAX_PAYLOAD:
            raise AuthenticationError("payload too large")
        claims = json.loads(raw)
        if not isinstance(claims, dict) or claims.get("v") != 1:
            raise AuthenticationError("bad claims")
        iat, exp = claims["iat"], claims["exp"]
        if isinstance(iat, bool) or isinstance(exp, bool):
            raise AuthenticationError("bad claim types")
        if not isinstance(iat, int) or not isinstance(exp, int):
            raise AuthenticationError("bad claim types")
        now = int(self._clock.now().timestamp())
        if exp <= now or iat > now + self._skew or exp - iat > self._max_ttl:
            raise AuthenticationError("token not valid now")
        roles_raw = claims["roles"]
        if not isinstance(roles_raw, list) or not roles_raw or len(roles_raw) > 8:
            raise AuthenticationError("bad roles")
        roles = frozenset(Role(str(item)) for item in roles_raw)  # unknown role -> ValueError
        try:
            return Principal(str(claims["sub"]), str(claims["tid"]), roles)
        except ValidationFailure as exc:
            raise AuthenticationError("bad identity claims") from exc
