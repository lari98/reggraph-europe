"""Signatures for stored results (HMAC-SHA256, key id for rotation).

====  ======================================================================================
G1    The signed message is domain-separated (``reggraph-result-v1|`` prefix), so a token
      signature can never be replayed as a result signature or the other way round.
G2    Constant-time comparison; an unknown key id or malformed signature is simply "invalid".
G3    The key id is part of the signature, so keys can be rotated: old results keep verifying
      as long as the old key stays in ``REGGRAPH_TOKEN_KEYS``.
====  ======================================================================================

A signature proves the result was produced by this deployment and has not been changed since.
It is not a legal e-signature.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Final

_DOMAIN: Final = b"reggraph-result-v1|"


class ResultSigner:
    def __init__(self, keys: Mapping[str, bytes], active_kid: str) -> None:
        if active_kid not in keys:
            raise ValueError("active key id is not among the keys")
        self._keys = dict(keys)
        self._active = active_kid

    def _mac(self, kid: str, message: str) -> str:
        key = self._keys[kid]
        return hmac.new(key, _DOMAIN + message.encode("utf-8"), hashlib.sha256).hexdigest()

    def sign(self, message: str) -> str:
        return f"{self._active}.{self._mac(self._active, message)}"

    def verify(self, message: str, signature: str) -> bool:
        kid, sep, digest = signature.partition(".")
        if not sep or kid not in self._keys:
            return False
        return hmac.compare_digest(self._mac(kid, message), digest)
