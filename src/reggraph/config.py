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
REGGRAPH_CONNECTOR_HOSTS    ECB, EUR-Lex  comma list of hosts connectors may call (exact names)
REGGRAPH_ECB_RATES_URL      ECB daily XML https URL of the ECB rate file (host must be allowed)
REGGRAPH_EURLEX_FEED_URL    (empty)       https URL of an EUR-Lex RSS/Atom feed; empty = off
REGGRAPH_OIDC_ISSUER        (empty)       identity provider issuer; empty = SSO off
REGGRAPH_OIDC_AUDIENCE      (empty)       audience this API expects in access tokens
REGGRAPH_OIDC_JWKS_URL      (empty)       https URL of the provider's public key set
REGGRAPH_OIDC_TENANT_CLAIM  reggraph_tenant   claim that carries the tenant id
REGGRAPH_OIDC_ROLES_CLAIM   reggraph_roles    claim that carries the role names (a list)
REGGRAPH_ALLOW_LOCAL_TOKENS true          accept RegGraph's own tokens (turn off once SSO is live)

In ``prod`` the service refuses to start with a missing, short or placeholder key, an in-memory
database, and it hides the interactive API docs.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Final

from reggraph.core.errors import ValidationFailure
from reggraph.netio import UpstreamError, validate_url

MIN_SECRET_CHARS: Final = 32
_PLACEHOLDERS: Final = ("change-me", "changeme", "secret", "password", "example")
DEFAULT_CONNECTOR_HOSTS: Final = ("www.ecb.europa.eu", "eur-lex.europa.eu")
DEFAULT_ECB_URL: Final = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_HOST_RE: Final = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_CLAIM_RE: Final = re.compile(r"^[A-Za-z0-9_:./-]{1,64}$")


def _is_host_name(host: str) -> bool:
    """A real DNS name: not an IP literal, not a wildcard, not ``localhost``-style."""
    if not _HOST_RE.match(host):
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return True
    return False


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    db_path: str = ":memory:"
    database_url: str = field(default="", repr=False)  # holds a password: never printed
    database_schema: str = "public"
    token_keys: dict[str, bytes] = field(default_factory=dict, repr=False)
    active_kid: str = ""
    rate_per_minute: int = 120
    rate_burst: int = 30
    max_body_bytes: int = 256 * 1024
    connector_hosts: tuple[str, ...] = DEFAULT_CONNECTOR_HOSTS
    ecb_rates_url: str = DEFAULT_ECB_URL
    eurlex_feed_url: str = ""
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_tenant_claim: str = "reggraph_tenant"
    oidc_roles_claim: str = "reggraph_roles"
    allow_local_tokens: bool = True

    def __post_init__(self) -> None:
        if self.env not in {"dev", "test", "prod"}:
            raise ValidationFailure("REGGRAPH_ENV must be dev, test or prod")
        if not self.token_keys or self.active_kid not in self.token_keys:
            raise ValidationFailure("signing keys are missing")
        for secret in self.token_keys.values():
            if len(secret) < MIN_SECRET_CHARS:
                raise ValidationFailure("signing key too short")
        if self.env == "prod":
            if self.db_path == ":memory:" and not self.database_url:
                raise ValidationFailure("prod needs a persistent database")
            for secret in self.token_keys.values():
                lowered = secret.decode("utf-8", "ignore").lower()
                if any(word in lowered for word in _PLACEHOLDERS):
                    raise ValidationFailure("placeholder signing key in prod")
        if not 1 <= self.rate_per_minute <= 100_000 or not 1 <= self.rate_burst <= 10_000:
            raise ValidationFailure("rate limits out of range")
        if not 1024 <= self.max_body_bytes <= 10 * 1024 * 1024:
            raise ValidationFailure("max body size out of range")

        self._check_connectors()
        self._check_oidc()

    def _check_connectors(self) -> None:
        for host in self.connector_hosts:
            if not _is_host_name(host):
                raise ValidationFailure("connector host must be an exact lower-case host name")
        for url in (self.ecb_rates_url, self.eurlex_feed_url):
            if url:
                try:
                    validate_url(url, self.connector_hosts)
                except UpstreamError as exc:
                    raise ValidationFailure(
                        "connector url must be https on an allowed host"
                    ) from exc

    def _check_oidc(self) -> None:
        fields = (self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url)
        if not any(fields):
            if not self.allow_local_tokens:
                raise ValidationFailure("local tokens cannot be switched off without SSO")
            return
        if not all(fields):
            raise ValidationFailure("OIDC needs issuer, audience and jwks url together")
        if not self.oidc_issuer.startswith("https://") or len(self.oidc_issuer) > 300:
            raise ValidationFailure("OIDC issuer must be an https url")
        host = self.oidc_jwks_host()
        if not _is_host_name(host):
            raise ValidationFailure("OIDC jwks url must be https on a named host")
        for claim in (self.oidc_tenant_claim, self.oidc_roles_claim):
            if not _CLAIM_RE.match(claim):
                raise ValidationFailure("OIDC claim names are limited to simple characters")
        if len(self.oidc_audience) > 200:
            raise ValidationFailure("OIDC audience too long")

    def oidc_jwks_host(self) -> str:
        """Host of the key-set url (the only host the OIDC fetcher may call)."""
        try:
            host, _ = validate_url(self.oidc_jwks_url, {self._raw_host(self.oidc_jwks_url)})
        except UpstreamError as exc:
            raise ValidationFailure("OIDC jwks url must be a plain https url") from exc
        return host

    @staticmethod
    def _raw_host(url: str) -> str:
        from urllib.parse import urlsplit

        return (urlsplit(url).hostname or "").lower()

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer)

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
            database_url=env.get("REGGRAPH_DATABASE_URL", ""),
            database_schema=env.get("REGGRAPH_DATABASE_SCHEMA", "public"),
            token_keys=keys,
            active_kid=active,
            rate_per_minute=int(env.get("REGGRAPH_RATE_PER_MINUTE", "120")),
            rate_burst=int(env.get("REGGRAPH_RATE_BURST", "30")),
            max_body_bytes=int(env.get("REGGRAPH_MAX_BODY_BYTES", str(256 * 1024))),
            connector_hosts=tuple(
                h.strip().lower()
                for h in env.get(
                    "REGGRAPH_CONNECTOR_HOSTS", ",".join(DEFAULT_CONNECTOR_HOSTS)
                ).split(",")
                if h.strip()
            ),
            ecb_rates_url=env.get("REGGRAPH_ECB_RATES_URL", DEFAULT_ECB_URL),
            eurlex_feed_url=env.get("REGGRAPH_EURLEX_FEED_URL", ""),
            oidc_issuer=env.get("REGGRAPH_OIDC_ISSUER", ""),
            oidc_audience=env.get("REGGRAPH_OIDC_AUDIENCE", ""),
            oidc_jwks_url=env.get("REGGRAPH_OIDC_JWKS_URL", ""),
            oidc_tenant_claim=env.get("REGGRAPH_OIDC_TENANT_CLAIM", "reggraph_tenant"),
            oidc_roles_claim=env.get("REGGRAPH_OIDC_ROLES_CLAIM", "reggraph_roles"),
            allow_local_tokens=env.get("REGGRAPH_ALLOW_LOCAL_TOKENS", "true").lower()
            not in {"0", "false", "no", "off"},
        )
