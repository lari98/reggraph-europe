"""Sandboxed HTTPS GET for connectors and the identity provider's key set.

Everything that leaves the platform goes through :class:`SafeFetcher`. It is deliberately
small and strict: the caller cannot choose a host, a port, a redirect or a size.

=====  ==============================================================================
N1     HTTPS only, port 443 only, no ``user:password@`` in the URL.
N2     The host must be on an explicit allow-list (exact match, no wildcards).
N3     The URL may not contain spaces, control characters, backslashes or non-ASCII
       (blocks header/request splitting and look-alike hosts).
N4     The host is resolved once and **every** address must be public. Loopback, private,
       link-local, multicast, reserved and cloud-metadata addresses are refused (SSRF).
N5     The connection is pinned to the checked address (no second DNS lookup, so no DNS
       rebinding), while TLS still verifies the certificate for the real host name.
N6     Redirects are never followed: a 3xx answer is an error.
N7     The body is limited in size and the whole transfer in time (slow-drip defence).
N8     Only the expected content types are accepted; compressed bodies are not requested.
N9     Failures raise :class:`UpstreamError` with a generic message; details go to logs only.
=====  ==============================================================================

The network pieces (``resolver`` and ``transport``) are injectable, so tests never touch
the real internet.
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import socket
import ssl
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Final, Protocol
from urllib.parse import urlsplit

from reggraph import __version__
from reggraph.core.errors import RegGraphError

log = logging.getLogger("reggraph.netio")

DEFAULT_MAX_BYTES: Final = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_DEADLINE_SECONDS: Final = 20.0
_MAX_URL_CHARS: Final = 2048
_CHUNK: Final = 64 * 1024


class UpstreamError(RegGraphError):
    """An outbound request was refused or failed. Never shown to API clients in detail."""

    http_status = 502
    code = "upstream_error"
    message_key = "error.internal"


@dataclass(frozen=True, slots=True)
class FetchResult:
    status: int
    content_type: str  # media type only, lower case, no parameters
    body: bytes


class Fetcher(Protocol):
    def get(self, url: str, accept: Collection[str]) -> FetchResult: ...


Resolver = Callable[[str], list[str]]
Transport = Callable[[str, str, int, str, "tuple[str, ...]", float, int], FetchResult]


def validate_url(url: str, allowed_hosts: Collection[str]) -> tuple[str, str]:
    """Return ``(host, path_and_query)`` for an acceptable URL, else raise ``UpstreamError``."""
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_CHARS:
        raise UpstreamError("url has a bad size")
    if any(ord(ch) <= 32 or ord(ch) >= 127 or ch == "\\" for ch in url):
        raise UpstreamError("url has forbidden characters")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UpstreamError("only https is allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UpstreamError("credentials in urls are refused")
    try:
        port = parts.port
    except ValueError as exc:
        raise UpstreamError("bad port") from exc
    if port not in (None, 443):
        raise UpstreamError("only port 443 is allowed")
    host = (parts.hostname or "").lower()
    if not host or host not in {h.lower() for h in allowed_hosts}:
        raise UpstreamError("host is not on the allow-list")
    if parts.fragment:
        raise UpstreamError("fragments are refused")
    path = parts.path or "/"
    return host, path + (f"?{parts.query}" if parts.query else "")


def _public_address(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


class _PinnedConnection(http.client.HTTPSConnection):
    """TLS connection that dials a fixed IP but verifies the certificate for ``host``."""

    def __init__(
        self, host: str, ip: str, port: int, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._pinned_ip = ip
        self._tls = context

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = self._tls.wrap_socket(raw, server_hostname=self.host)


def https_transport(
    host: str,
    ip: str,
    port: int,
    path: str,
    accept: tuple[str, ...],
    timeout: float,
    max_bytes: int,
    *,
    context: ssl.SSLContext | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> FetchResult:
    """The real network call. ``context`` is injectable for tests only."""
    tls = context or ssl.create_default_context()
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    conn = _PinnedConnection(host, ip, port, timeout, tls)
    try:
        conn.request(
            "GET",
            path,
            headers={
                "Host": host,
                "Accept": ", ".join(accept),
                "Accept-Encoding": "identity",
                "User-Agent": f"RegGraph-Europe/{__version__}",
            },
        )
        response = conn.getresponse()
        deadline = time.monotonic() + deadline_seconds
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise UpstreamError("response too large")
            if time.monotonic() > deadline:
                raise UpstreamError("response too slow")
            chunks.append(chunk)
        media = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        return FetchResult(response.status, media, b"".join(chunks))
    finally:
        conn.close()


class OfflineFetcher:
    """Used in offline (travel) mode: there is no code path that can reach the network."""

    def get(self, url: str, accept: Collection[str]) -> FetchResult:
        raise UpstreamError("offline mode: outbound access is switched off")


class SafeFetcher:
    def __init__(
        self,
        allowed_hosts: Collection[str],
        *,
        resolver: Resolver | None = None,
        transport: Transport | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._hosts = frozenset(h.lower() for h in allowed_hosts)
        self._resolve = resolver or system_resolver
        self._transport = transport or https_transport
        self._max_bytes = max_bytes
        self._timeout = timeout

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return self._hosts

    def get(self, url: str, accept: Collection[str]) -> FetchResult:
        host, path = validate_url(url, self._hosts)
        wanted = tuple(sorted(a.lower() for a in accept))
        if not wanted:
            raise UpstreamError("no content type accepted")
        try:
            addresses = self._resolve(host)
        except OSError as exc:
            log.warning("dns lookup failed host=%s", host)
            raise UpstreamError("name resolution failed") from exc
        if not addresses or not all(_public_address(a) for a in addresses):
            log.warning("refused non-public address for host=%s", host)
            raise UpstreamError("host resolves to a non-public address")
        try:
            result = self._transport(
                host, addresses[0], 443, path, wanted, self._timeout, self._max_bytes
            )
        except UpstreamError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
            log.warning("fetch failed host=%s error=%s", host, type(exc).__name__)
            raise UpstreamError("transfer failed") from exc
        if 300 <= result.status < 400:
            raise UpstreamError("redirects are refused")
        if result.status != 200:
            raise UpstreamError(f"unexpected status {result.status}")
        if len(result.body) > self._max_bytes:
            raise UpstreamError("response too large")
        if result.content_type not in wanted:
            raise UpstreamError("unexpected content type")
        return result
