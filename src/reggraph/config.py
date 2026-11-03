"""Runtime settings, read from environment variables and validated at start-up.

Variable                    Default       Meaning
--------------------------  ------------  -------------------------------------------------
REGGRAPH_ENV                dev           dev | test | prod
REGGRAPH_DB_PATH            :memory:      SQLite file (PostgreSQL arrives in Phase 4)
REGGRAPH_TOKEN_KEYS         (required)    ``kid:secret,kid2:secret2``; secrets >= 32 chars
REGGRAPH_ACTIVE_KID         (first key)   key id used to sign new tokens
REGGRAPH_RATE_PER_MINUTE    120           sustained requests per minute per client
REGGRAPH_RATE_BURST         30            burst size per client
REGGRAPH_MAX_BODY_BYTES     262144        largest accepted request body (256 KiB)

In ``prod`` the service refuses to start with a missing, short or placeholder key, an in-memory
database, and it hides the interactive API docs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from reggraph.core.errors import ValidationFailure

MIN_SECRET_CHARS: Final = 32
_PLACEHOLDERS: Final = ("change-me", "changeme", "secret", "password", "example")


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    db_path: str = ":memory:"
    token_keys: dict[str, bytes] = field(default_factory=dict)
    active_kid: str = ""
    rate_per_minute: int = 120
    rate_burst: int = 30
    max_body_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if self.env not in {"dev", "test", "prod"}:
            raise ValidationFailure("REGGRAPH_ENV must be dev, test or prod")
        if not self.token_keys or self.active_kid not in self.token_keys:
            raise ValidationFailure("signing keys are missing")
        for secret in self.token_keys.values():
            if len(secret) < MIN_SECRET_CHARS:
                raise ValidationFailure("signing key too short")
        if self.env == "prod":
            if self.db_path == ":memory:":
                raise ValidationFailure("prod needs a persistent database")
            for secret in self.token_keys.values():
                lowered = secret.decode("utf-8", "ignore").lower()
                if any(word in lowered for word in _PLACEHOLDERS):
                    raise ValidationFailure("placeholder signing key in prod")
        if not 1 <= self.rate_per_minute <= 100_000 or not 1 <= self.rate_burst <= 10_000:
            raise ValidationFailure("rate limits out of range")
        if not 1024 <= self.max_body_bytes <= 10 * 1024 * 1024:
            raise ValidationFailure("max body size out of range")

    @property
    def docs_enabled(self) -> bool:
        return self.env != "prod"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = environ if environ is not None else dict(os.environ)
        raw = env.get("REGGRAPH_TOKEN_KEYS", "")
        keys: dict[str, bytes] = {}
        for item in filter(None, (part.strip() for part in raw.split(","))):
            kid, sep, secret = item.partition(":")
            if not sep:
                raise ValidationFailure("REGGRAPH_TOKEN_KEYS must look like kid:secret")
            keys[kid] = secret.encode("utf-8")
        active = env.get("REGGRAPH_ACTIVE_KID") or next(iter(keys), "")
        return cls(
            env=env.get("REGGRAPH_ENV", "dev"),
            db_path=env.get("REGGRAPH_DB_PATH", ":memory:"),
            token_keys=keys,
            active_kid=active,
            rate_per_minute=int(env.get("REGGRAPH_RATE_PER_MINUTE", "120")),
            rate_burst=int(env.get("REGGRAPH_RATE_BURST", "30")),
            max_body_bytes=int(env.get("REGGRAPH_MAX_BODY_BYTES", str(256 * 1024))),
        )
